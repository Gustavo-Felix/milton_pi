"""
FASE 09 — Testes da Pipeline ANS (sem rede e sem banco).

Os DataFrames de entrada usam os cabeçalhos reais publicados no PDA da ANS.

Execute:
    .venv\\Scripts\\python -m pytest src\\tests.py -v
"""

import zipfile
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table

from src import transforms as T
from src.downloader import detect_csv_params, iter_csv_sources, select_files
from src.loader import Loader, _to_rows


# =============================================================================
# Helpers de transformação
# =============================================================================

def test_decimal_formato_brasileiro():
    s = pd.Series(["263,64", "1.581,60", "41.78", "", None])
    out = T._decimal(s)
    assert out.iloc[:3].tolist() == [263.64, 1581.6, 41.78]
    assert out.iloc[3:].isna().all()


def test_stable_id_deterministico_e_cabe_em_bigint():
    df = pd.DataFrame({"a": [1, 2, 1], "b": ["x", "y", "x"]})
    ids = T.stable_id(df, ["a", "b"])
    assert ids.iloc[0] == ids.iloc[2] != ids.iloc[1]
    assert (ids >= 0).all() and ids.dtype == "int64"
    assert T.stable_id(df, ["a", "b"]).tolist() == ids.tolist()
    # valor fixo: se uma versão nova do pandas mudar o hash, os ids do banco mudariam e duplicariam linhas
    assert T.stable_id(pd.DataFrame({"a": ["1", "2"], "b": [10, None]}), ["a", "b"]).tolist() == [
        4232106699303927704, 5628148740073565286]


def test_upsert_so_reescreve_linha_que_mudou():
    from sqlalchemy import DateTime
    from sqlalchemy.dialects import postgresql
    table = Table("produto_saude", MetaData(), Column("id", Integer), Column("nome", String),
                  Column("valor", Integer), Column("updated_at", DateTime))
    conn = type("Conn", (), {"dialect": postgresql.dialect()})()
    sql = Loader(conn)._upsert_sql(table, ["id", "nome", "valor"], ["id"], "update")
    assert sql.startswith("INSERT INTO produto_saude AS cur (id, nome, valor) VALUES %s ON CONFLICT (id) DO UPDATE SET ")
    assert "updated_at = CURRENT_TIMESTAMP" in sql
    assert sql.endswith("WHERE (cur.nome, cur.valor) IS DISTINCT FROM (EXCLUDED.nome, EXCLUDED.valor)")
    assert Loader(conn)._upsert_sql(table, ["id"], ["id"], "update").endswith("ON CONFLICT (id) DO NOTHING")


def test_aggregator_soma_entre_chunks():
    agg = T.Aggregator(["k"], ["v"], compact_every=2)
    for _ in range(5):
        agg.add(pd.DataFrame({"k": [1, 2], "v": [1, 10]}))
    out = agg.result().set_index("k")["v"].to_dict()
    assert out == {1: 5, 2: 50}


# =============================================================================
# Transformações por dataset
# =============================================================================

def test_geografia():
    df = pd.DataFrame({
        "CD_MUNICIPIO": ["110001", "110002", "355030"],
        "NM_MUNICIPIO": ["Alta Floresta D'Oeste", "Ariquemes", "São Paulo"],
        "SG_UF": ["RO", "RO", "SP"], "NM_UF": ["Rondônia", "Rondônia", "São Paulo"],
        "NM_REGIAO": ["Norte", "Norte", "Sudeste"],
    })
    uf, mun = T.transform_geografia(df)
    assert sorted(uf["id"].tolist()) == [11, 35]
    assert mun.set_index("id").loc[355030, "id_estado"] == 35
    assert "codigo_ibge" not in mun  # o código oficial de 7 dígitos vem da API do IBGE


def test_operadora():
    df = pd.DataFrame({
        "REGISTRO_OPERADORA": ["419761", "419761", None],
        "CNPJ": ["19541931000125"] * 3, "RAZAO_SOCIAL": ["18 DE JULHO"] * 3,
        "NOME_FANTASIA": [None] * 3, "MODALIDADE": ["Administradora de Benefícios"] * 3,
        "UF": ["MG"] * 3, "DATA_REGISTRO_ANS": ["2015-05-19"] * 3,
    })
    out = T.transform_operadora(df)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["id"] == 419761 and row["registro_ans"] == "419761"
    assert str(row["data_registro"]) == "2015-05-19"


def test_produto_saude_e_operadora_referenciada():
    df = pd.DataFrame({
        "ID_PLANO": ["1523381", "1523382"], "CD_PLANO": ["3130", "3131"],
        "NM_PLANO": ["SEC-UNIPOP", "X"], "REGISTRO_OPERADORA": ["364070", "5711"],
        "RAZAO_SOCIAL": ["Unimed Três Pontas", "Bradesco"], "GR_MODALIDADE": ["Cooperativa Médica", "Seguradora"],
        "CONTRATACAO": ["Individual ou familiar"] * 2, "SGMT_ASSISTENCIAL": ["Ambulatorial"] * 2,
        "COBERTURA": ["Médico-hospitalar"] * 2,
        "ABRANGENCIA_COBERTURA": ["Grupo de municípios", "	Nacional	"],
        "FATOR_MODERADOR": ["Coparticipação", "Ausente"],
        "SITUACAO_PLANO": ["Ativo", "Cancelado"],
    })
    prod, ops = T.transform_produto_saude(df)
    assert prod["possui_copart"].tolist() == [True, False]
    assert prod["ativo"].tolist() == [True, False]
    assert prod["abrangencia_geografica"].tolist() == ["Grupo de municípios", "Nacional"]
    assert ops.set_index("id").loc[5711, "registro_ans"] == "005711"


def test_abrangencia_ignora_tab_e_duplicatas():
    df = pd.DataFrame({"ID_PLANO": ["1325224", "1325224", "x"], "CD_MUNICIPIO": ["270010", "270010", "270020"]})
    out = T.transform_abrangencia_plano(df)
    assert out.to_dict("records") == [{"id_produto_saude": 1325224, "id_municipio": 270010,
                                       "tipo_abrangencia": "COMERCIALIZACAO_ATIVA"}]


def test_preco_faixa_etaria_media_entre_municipios():
    agg = T.Aggregator(["id_produto_saude", "cd_faixa"], ["soma", "n"])
    agg.add(T.transform_vcm_chunk(pd.DataFrame({
        "ID_PLANO": ["1328693", "1328693", "1328693"],
        "CD_FAIXA_ETARIA": ["1", "1", "10"],
        "VCM": ["263,64", "300,00", "1581,6"],
    })))
    out = T.finalize_preco_faixa_etaria(agg.result(), 202609).set_index("faixa_etaria")
    assert out.loc["0-18", "valor_mensalidade"] == pytest.approx(281.82)
    assert out.loc["59+", "valor_mensalidade"] == pytest.approx(1581.6)
    assert (out["id_serie_mensal"] == 202609).all()
    assert out.loc["0-18", "faixa_etaria_inicial"] == 0 and out.loc["0-18", "faixa_etaria_final"] == 18
    assert out.loc["59+", "faixa_etaria_inicial"] == 59 and pd.isna(out.loc["59+", "faixa_etaria_final"])


def test_rede_credenciada_agregacao_sintetica():
    df = pd.DataFrame({
        "ID_PLANO": ["16790991"] * 6 + ["1"],
        "CD_MUNICIPIO": ["120040"] * 4 + ["120050", "120040", None],
        "DE_CLAS_ESTB_SAUDE": ["Assistencia Hospitalar", "Hospital Dia", "LABORATORIO X", "Outros",
                               "Demais Estabelecimentos", "Assistencia Hospitalar", "Hospital"],
        "DE_TIPO_PRESTADOR": ["", "", "", "Consultorio isolado", "Proprio", "", ""],
        "DT_ATUALIZACAO": ["2026-10-24"] * 5 + ["2026-11-02", "2026-10-24"],
    })
    agg = T.Aggregator(list(T.REDE_KEYS), list(T.REDE_QTD_COLS))
    agg.add(T.transform_rede_credenciada_chunk(df.iloc[:2]))
    agg.add(T.transform_rede_credenciada_chunk(df.iloc[2:]))
    out = T.finalize_rede_credenciada(agg.result()).set_index(["id_serie_mensal", "id_municipio"])
    assert out.loc[(202610, 120040), list(T.REDE_QTD_COLS)].tolist() == [2, 0, 1, 1]
    # par presente sem nenhum dos tipos: fica com zeros (SELECT do instrucoes.md)
    assert out.loc[(202610, 120050), list(T.REDE_QTD_COLS)].tolist() == [0, 0, 0, 0]
    # período vem do DT_ATUALIZACAO; outro mês = outra linha (histórico mensal)
    assert out.loc[(202611, 120040), "qtd_hospitais"] == 1
    assert len(out) == 3 and out["id"].is_unique


def test_market_share():
    df = pd.DataFrame({
        "ID_TEMPO_COMPETENCIA": ["2026-08"] * 4,
        "REGISTRO_OPERADORA": ["416401", "416401", "337871", "416401"],
        "CD_MUNICIPIO": ["120040", "120040", "120040", "120050"],
    })
    agg = T.Aggregator(["competencia", "id_operadora", "id_municipio"], ["total_beneficiarios"])
    agg.add(T.transform_sib_chunk(df))
    out = T.finalize_market_share(agg.result())
    assert (out["id_serie_mensal"] == 202608).all()  # período do arquivo, não o da execução
    out = out.set_index(["id_operadora", "id_municipio"])
    assert out.loc[(416401, 120040), "total_beneficiarios"] == 2
    assert out.loc[(416401, 120040), "percentual_market_share"] == pytest.approx(0.6667)
    assert out.loc[(416401, 120050), "percentual_market_share"] == 1.0


def test_taxa_cobertura():
    df = pd.DataFrame({
        "PERIODO": ["2025", "2025", "2026"], "CD_MUNICIPIO": ["120005"] * 3,
        "BENEF_TOTAL": ["10", "15", "3"], "POPULACAO": ["50", "50", "0"],
    })
    agg = T.Aggregator(["ano", "id_municipio"], ["benef", "populacao"])
    agg.add(T.transform_taxa_cobertura_chunk(df))
    out = T.finalize_perfil_saude_publica(agg.result()).set_index("id_serie_anual")
    assert out.loc[2025, "taxa_cobertura_plano_saude"] == pytest.approx(0.25)
    assert pd.isna(out.loc[2026, "taxa_cobertura_plano_saude"])


# =============================================================================
# Downloader
# =============================================================================

def test_detect_utf8_e_latin1():
    assert detect_csv_params("A;B;C\nção;1;2\n".encode("utf-8")) == ("utf-8", ";")
    # 0xe3 = 'ã' em latin1: antes era classificado como utf-8 e quebrava a leitura
    assert detect_csv_params("PERIODO;NM\n2026;Região\n".encode("cp1252"))[0] == "cp1252"
    assert detect_csv_params(b"a,b,c\n1,2,3\n") == ("utf-8", ",")


def test_select_files():
    names = ["201406.zip", "202512.zip", "202606.zip", "pda-054-geografia.csv", "dic.ods"]
    assert select_files(names, r"^\d{6}\.zip$", "competencia", 202603) == ["202512.zip"]
    assert select_files(names, r"^\d{6}\.zip$", "competencia", 201001) == ["202606.zip"]
    assert select_files(names, r"geografia", "primeiro") == ["pda-054-geografia.csv"]
    assert select_files(["sib_ativo_SP.zip", "sib_ativo_AC.zip", "sib_inativo_AC.zip"],
                        r"^sib_ativo_[A-Z]{2}\.zip$", "todos") == ["sib_ativo_AC.zip", "sib_ativo_SP.zip"]


def test_zip_com_varios_csv_em_chunks_e_usecols(tmp_path: Path):
    zpath = tmp_path / "rede.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for uf, n in (("AC", 2500), ("AL", 10)):
            body = "ID_PLANO;CD_MUNICIPIO;OUTRA\n" + "".join(f"{i};120040;x\n" for i in range(n))
            zf.writestr(f"produtos_prestadores_hospitalares_{uf}.csv", body)

    seen = {}
    for name, chunks in iter_csv_sources(zpath, usecols=["ID_PLANO", "CD_MUNICIPIO"], chunk_size=1000):
        sizes = []
        for c in chunks:
            assert list(c.columns) == ["ID_PLANO", "CD_MUNICIPIO"]
            sizes.append(len(c))
        seen[name] = sizes
    assert seen == {"produtos_prestadores_hospitalares_AC.csv": [1000, 1000, 500],
                    "produtos_prestadores_hospitalares_AL.csv": [10]}
    zpath.unlink()  # handle fechado -> remoção funciona no Windows


def test_abandonar_iteracao_fecha_arquivo(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("A;B\n" + "1;2\n" * 3000, encoding="utf-8")
    sources = iter_csv_sources(p, chunk_size=1000)
    _, chunks = next(sources)
    next(chunks)
    sources.close()
    p.unlink()


# =============================================================================
# Loader (sem banco)
# =============================================================================

def test_to_records_tipos_nativos():
    df = pd.DataFrame({
        "a": pd.array([1, None], dtype="Int64"),
        "b": [1.5, float("nan")],
        "c": pd.array(["x", None], dtype="string"),
    })
    rows = _to_rows(df)
    assert rows == [(1, 1.5, "x"), (None, None, None)]
    assert type(rows[0][0]) is int


def test_prepare_trunca_varchar_e_remove_colunas_extras():
    table = Table("t", MetaData(), Column("id", Integer, primary_key=True), Column("nome", String(5)))
    loader = Loader.__new__(Loader)
    out = loader._prepare(table, pd.DataFrame({"id": [1], "nome": ["abcdefgh"], "extra": [0]}))
    assert list(out.columns) == ["id", "nome"]
    assert out["nome"].iloc[0] == "abcde"


def test_ibge_localidades():
    est = T.transform_ibge_estados([{"id": 35, "sigla": "SP", "nome": "São Paulo", "regiao": {"nome": "Sudeste"}}])
    assert est.iloc[0].to_dict() == {"id": 35, "codigo_ibge": "35", "sigla": "SP", "nome": "São Paulo", "regiao": "Sudeste"}
    mun = T.transform_ibge_municipios([{"id": 3552205, "nome": "Sorocaba",
                                        "microrregiao": {"mesorregiao": {"UF": {"id": 35}}}}])
    assert mun.iloc[0].to_dict() == {"id": 355220, "codigo_ibge": "3552205", "nome": "Sorocaba", "id_estado": 35}


def test_taxa_usa_populacao_ibge_do_mesmo_ano():
    sidra = [{"resultados": [{"series": [
        {"localidade": {"id": "1200050"}, "serie": {"2026": "100"}},
        {"localidade": {"id": "1200203"}, "serie": {"2026": "..."}},
    ]}]}]
    pop = T.transform_sidra_populacao(sidra)
    assert pop.to_dict("records") == [{"id_municipio": 120005, "ano": 2026, "populacao": 100}]
    agg = pd.DataFrame({"ano": [2026, 2025], "id_municipio": [120005, 120005], "benef": [25, 10], "populacao": [0, 0]})
    out = T.finalize_perfil_saude_publica(agg, pop).set_index("id_serie_anual")
    assert out.loc[2026, "taxa_cobertura_plano_saude"] == pytest.approx(0.25)
    assert pd.isna(out.loc[2025, "taxa_cobertura_plano_saude"])  # não mistura anos


def test_populacao_anual_e_vigente():
    sidra = [{"resultados": [{"series": [
        {"localidade": {"id": "1200050"}, "serie": {"2024": "90", "2025": "100", "2022": "-"}},
    ]}]}]
    pop = T.transform_sidra_populacao(sidra)
    serie = T.finalize_populacao_anual(pop).set_index("id_serie_anual")
    assert serie["populacao_estimada"].to_dict() == {2024: 90, 2025: 100}  # ausência fica vazia
    assert T.populacao_vigente(pop).to_dict("records") == [{"id": 120005, "populacao_estimada": 100, "id_serie_anual": 2025}]
    # mesmo id da linha da taxa de cobertura (município, ano) -> a mesma linha recebe as duas colunas
    agg = pd.DataFrame({"ano": pd.array([2025], dtype="Int64"), "id_municipio": pd.array([120005], dtype="Int64"),
                        "benef": [10], "populacao": [0]})
    taxa = T.finalize_perfil_saude_publica(agg, pop)
    assert taxa["id"].iloc[0] == serie.loc[2025, "id"]


def test_sidra_pede_em_lotes_de_ate_8_periodos(monkeypatch):
    from src import orchestrator as O
    anos = [str(a) for a in range(2005, 2027)]  # 22 períodos, como o 6579
    pedidos = []

    def fake_get_json(ctx, url):
        if url.endswith("/periodos"):
            return [{"id": a} for a in anos]
        lote = url.split("/periodos/")[1].split("/")[0].split("|")
        pedidos.append(lote)
        return [{"resultados": [{"series": [{"localidade": {"id": "1200050"}, "serie": {a: "10" for a in lote}}]}]}]

    monkeypatch.setattr(O, "_get_json", fake_get_json)
    cfg = {"url_populacao_periodos": "x/periodos", "url_populacao": "x/periodos/{periodos}/variaveis/9324"}
    pop = O._get_sidra_serie(None, cfg)
    assert [len(p) for p in pedidos] == [8, 8, 6]
    assert sorted(pop["ano"]) == list(range(2005, 2027))


def test_url_do_rds_vira_psycopg2_com_ssl(monkeypatch):
    from src import database as D
    monkeypatch.setattr(D, "_load_dotenv", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgres://milton:s%40nha@milton-pi.abc123.sa-east-1.rds.amazonaws.com:5432/milton_pi")
    url, args = D.engine_options(D.database_url())
    assert url.drivername == "postgresql+psycopg2" and url.password == "s@nha"
    assert args["sslmode"] == "require" and args["keepalives"] == 1
    assert D.engine_options("postgresql+psycopg2://u:p@localhost:55432/milton_pi")[1] == {}
    assert "sslmode" not in D.engine_options("postgresql://u:p@h.rds.amazonaws.com:5432/milton_pi?sslmode=verify-full")[1]
