"""
FASE 05/06 — Staging, Transformações e Mapeamentos De-Para.

Cada `transform_*` recebe um chunk bruto (colunas como no CSV da ANS, tudo
string) e devolve um DataFrame com as colunas do DDL em infra/init.sql.
Datasets que precisam de agregação sobre o arquivo inteiro usam `Aggregator`,
que mantém em memória apenas o resultado agregado (nunca as linhas brutas).

Cabeçalhos reais conferidos nos arquivos publicados no PDA da ANS.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

_INT64_MASK = 0x7FFF_FFFF_FFFF_FFFF

# ---------------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------------

def _str(series: pd.Series) -> pd.Series:
    """Strip (inclui tabs que a ANS deixa dentro das aspas) e vazio -> NA."""
    s = series.astype("string").str.strip()
    return s.mask(s == "")


def _int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(_str(series), errors="coerce").astype("Int64")


def _decimal(series: pd.Series) -> pd.Series:
    """Número no formato brasileiro ('1.234,56' ou '263,64') -> float."""
    s = _str(series)
    s = s.where(~s.str.contains(",", na=False), s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    return pd.to_numeric(s, errors="coerce")


def _date(series: pd.Series, fmt: Optional[str] = None) -> pd.Series:
    return pd.to_datetime(_str(series), format=fmt, errors="coerce").dt.date


def stable_id(df: pd.DataFrame, cols: Sequence[str]) -> pd.Series:
    """ID BIGINT determinístico a partir da chave natural (idempotência).

    As tabelas fato do DDL só têm `id` como PK; gerar o id a partir da chave
    natural permite `ON CONFLICT (id)` sem alterar o schema.
    """
    keys = df[list(cols)].astype("string")
    hashed = pd.util.hash_pandas_object(keys, index=False)
    return (hashed & _INT64_MASK).astype("int64")


class Aggregator:
    """Soma incremental por chave, com memória limitada ao nº de chaves distintas."""

    def __init__(self, keys: Sequence[str], values: Sequence[str], compact_every: int = 200):
        self.keys = list(keys)
        self.values = list(values)
        self._parts: list[pd.DataFrame] = []
        self._compact_every = compact_every

    def add(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        self._parts.append(df.groupby(self.keys, as_index=False)[self.values].sum())
        if len(self._parts) >= self._compact_every:
            self._parts = [self._combine()]

    def _combine(self) -> pd.DataFrame:
        return pd.concat(self._parts, ignore_index=True).groupby(self.keys, as_index=False)[self.values].sum()

    def result(self) -> pd.DataFrame:
        if not self._parts:
            return pd.DataFrame(columns=self.keys + self.values)
        out = self._combine()
        self._parts.clear()
        return out


# ---------------------------------------------------------------------------
# ESTADO / MUNICIPIO  (pda-054-geografia.csv)
# instrucoes.md § Dimensões Básicas
# ---------------------------------------------------------------------------

GEOGRAFIA_COLS = ("CD_MUNICIPIO", "NM_MUNICIPIO", "SG_UF", "NM_UF", "NM_REGIAO")


def transform_geografia(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retorna (estados, municipios). Código IBGE da UF = 2 primeiros dígitos."""
    mun = pd.DataFrame({
        "id": _int(df["CD_MUNICIPIO"]),
        "nome": _str(df["NM_MUNICIPIO"]),
    })
    mun["id_estado"] = mun["id"] // 10_000
    mun = mun.dropna(subset=["id"]).drop_duplicates("id")

    uf = pd.DataFrame({
        "id": mun["id_estado"],
        "sigla": _str(df.loc[mun.index, "SG_UF"]),
        "nome": _str(df.loc[mun.index, "NM_UF"]),
        "regiao": _str(df.loc[mun.index, "NM_REGIAO"]),
    })
    uf["codigo_ibge"] = uf["id"].astype("string")
    uf = uf.dropna(subset=["id"]).drop_duplicates("id")
    return uf.reset_index(drop=True), mun.reset_index(drop=True)


# ---------------------------------------------------------------------------
# OPERADORA  (Relatorio_cadop.csv — somente operadoras ativas)
# ---------------------------------------------------------------------------

OPERADORA_COLS = (
    "REGISTRO_OPERADORA", "CNPJ", "RAZAO_SOCIAL", "NOME_FANTASIA",
    "MODALIDADE", "UF", "DATA_REGISTRO_ANS",
)


def transform_operadora(df: pd.DataFrame, situacao: str = "ATIVA") -> pd.DataFrame:
    out = pd.DataFrame({
        "registro_ans": _str(df["REGISTRO_OPERADORA"]),
        "cnpj": _str(df["CNPJ"]),
        "razao_social": _str(df["RAZAO_SOCIAL"]),
        "nome_fantasia": _str(df["NOME_FANTASIA"]),
        "modalidade": _str(df["MODALIDADE"]),
        "uf_sede": _str(df["UF"]),
        "data_registro": _date(df["DATA_REGISTRO_ANS"], "%Y-%m-%d"),
    })
    out["id"] = _int(out["registro_ans"])
    out["situacao"] = situacao  # cada arquivo do PDA publica uma única situação
    return out.dropna(subset=["id"]).drop_duplicates("id", keep="last").reset_index(drop=True)


# ---------------------------------------------------------------------------
# PRODUTO_SAUDE  (pda-008-caracteristicas_produtos_saude_suplementar.csv)
# ---------------------------------------------------------------------------

PRODUTO_SAUDE_COLS = (
    "ID_PLANO", "CD_PLANO", "NM_PLANO", "REGISTRO_OPERADORA", "RAZAO_SOCIAL",
    "GR_MODALIDADE", "CONTRATACAO", "SGMT_ASSISTENCIAL", "COBERTURA",
    "ABRANGENCIA_COBERTURA", "FATOR_MODERADOR", "SITUACAO_PLANO",
)


def transform_produto_saude(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retorna (produtos, operadoras referenciadas).

    O arquivo inclui planos de operadoras canceladas, que não estão no
    Relatorio_cadop; elas são devolvidas para satisfazer a FK
    produto_saude.id_operadora com os dados presentes no próprio arquivo.
    """
    fator = _str(df["FATOR_MODERADOR"]).str.upper()
    situacao = _str(df["SITUACAO_PLANO"]).str.upper()
    prod = pd.DataFrame({
        "id": _int(df["ID_PLANO"]),
        "codigo_plano_ans": _str(df["CD_PLANO"]),
        "nome_plano": _str(df["NM_PLANO"]),
        "id_operadora": _int(df["REGISTRO_OPERADORA"]),
        "tipo_contratacao": _str(df["CONTRATACAO"]),
        "segmentacao": _str(df["SGMT_ASSISTENCIAL"]),
        "cobertura": _str(df["COBERTURA"]),
        "abrangencia_geografica": _str(df["ABRANGENCIA_COBERTURA"]),  # instrucoes.md § PLANO_SAUDE
        "possui_copart": fator.str.contains("COPARTICIPA", na=False).where(fator.notna()),
        "ativo": situacao.str.startswith("ATIVO").where(situacao.notna()),
    })
    prod = prod.dropna(subset=["id"]).drop_duplicates("id", keep="last")

    ops = pd.DataFrame({
        "id": prod["id_operadora"],
        "razao_social": _str(df.loc[prod.index, "RAZAO_SOCIAL"]),
        "modalidade": _str(df.loc[prod.index, "GR_MODALIDADE"]),
    })
    ops["registro_ans"] = ops["id"].astype("string").str.zfill(6)
    ops = ops.dropna(subset=["id"]).drop_duplicates("id")
    return prod.reset_index(drop=True), ops.reset_index(drop=True)


# ---------------------------------------------------------------------------
# ABRANGENCIA_PLANO  (pda_area_comer_plano_ntrp.csv)
# ---------------------------------------------------------------------------

ABRANGENCIA_COLS = ("ID_PLANO", "CD_MUNICIPIO")
TIPO_ABRANGENCIA = "COMERCIALIZACAO_ATIVA"  # instrucoes.md: o arquivo lista a área de comercialização ativa


def transform_abrangencia_plano(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "id_produto_saude": _int(df["ID_PLANO"]),
        "id_municipio": _int(df["CD_MUNICIPIO"]),
    })
    out = out.dropna().drop_duplicates().reset_index(drop=True)
    out["tipo_abrangencia"] = TIPO_ABRANGENCIA
    return out


# ---------------------------------------------------------------------------
# PRECO_FAIXA_ETARIA  (valor_comercial_medio_por_municipio_NTRP-054/YYYYMM.zip)
# instrucoes.md § PRECO_FAIXA_ETARIA + Regra 3
# O VCM é publicado por (município, plano, faixa); o DDL guarda por
# (plano, faixa, competência) -> média do VCM entre os municípios.
# ---------------------------------------------------------------------------

VCM_COLS = ("ID_PLANO", "CD_FAIXA_ETARIA", "VCM")

# Faixas etárias padrão ANS (RN 63/2003), códigos 1..10 do PDA 054
FAIXAS_ANS = {
    1: "0-18", 2: "19-23", 3: "24-28", 4: "29-33", 5: "34-38",
    6: "39-43", 7: "44-48", 8: "49-53", 9: "54-58", 10: "59+",
}

def transform_vcm_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Chunk -> parciais (id_produto_saude, cd_faixa, soma, n) para `Aggregator`."""
    out = pd.DataFrame({
        "id_produto_saude": _int(df["ID_PLANO"]),
        "cd_faixa": _int(df["CD_FAIXA_ETARIA"]),
        "soma": _decimal(df["VCM"]),
    }).dropna()
    out["n"] = 1
    return out


def finalize_preco_faixa_etaria(agg: pd.DataFrame, id_serie_mensal: int) -> pd.DataFrame:
    if agg.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "id_produto_saude": agg["id_produto_saude"],
        "faixa_etaria": agg["cd_faixa"].astype(int).map(FAIXAS_ANS),
        "valor_mensalidade": (agg["soma"] / agg["n"]).round(2),
    })
    out["faixa_etaria"] = out["faixa_etaria"].fillna(agg["cd_faixa"].astype("string"))
    limites = out["faixa_etaria"].str.extract(r"^(\d+)(?:-(\d+))?")
    out["faixa_etaria_inicial"] = pd.to_numeric(limites[0], errors="coerce").astype("Int64")
    out["faixa_etaria_final"] = pd.to_numeric(limites[1], errors="coerce").astype("Int64")  # "59+" fica sem fim
    out["id_serie_mensal"] = id_serie_mensal
    out["id"] = stable_id(out, ["id_produto_saude", "id_serie_mensal", "faixa_etaria"])
    return out


# ---------------------------------------------------------------------------
# REDE_CREDENCIADA_SINTETICA  (produtos_e_prestadores_hospitalares.zip ~1.5 GB,
# um CSV por UF). Lógica SQL do instrucoes.md aplicada por chunk.
# ---------------------------------------------------------------------------

REDE_CRED_COLS = ("ID_PLANO", "CD_MUNICIPIO", "DE_CLAS_ESTB_SAUDE", "DE_TIPO_PRESTADOR", "DT_ATUALIZACAO")
REDE_FONTE = "ANS_PRESTADORES_HOSPITALARES"


REDE_KEYS = ("competencia", "id_produto_saude", "id_municipio")
REDE_QTD_COLS = ("qtd_hospitais", "qtd_pronto_atendimento", "qtd_laboratorios", "qtd_consultorios")


def transform_rede_credenciada_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Chunk bruto -> (competencia, id_produto_saude, id_municipio, qtd_*) no formato do DER.

    Mesmo SELECT do instrucoes.md: todo par (plano, município) presente no
    arquivo vira uma linha, ainda que nenhum dos quatro tipos case.
    O arquivo não tem competência; DT_ATUALIZACAO ("data da última atualização
    do conjunto de dados", dicionário ANS) dá o mês do snapshot (YYYYMM).
    """
    clas = _str(df["DE_CLAS_ESTB_SAUDE"]).str.upper().fillna("")
    tipo = _str(df["DE_TIPO_PRESTADOR"]).str.upper().fillna("")
    flags = pd.DataFrame({
        "competencia": _int(_str(df["DT_ATUALIZACAO"]).str.slice(0, 7).str.replace("-", "", regex=False)),
        "id_produto_saude": _int(df["ID_PLANO"]),
        "id_municipio": _int(df["CD_MUNICIPIO"]),
        "qtd_hospitais": clas.str.contains("HOSPITAL", regex=False).astype("int32"),
        "qtd_pronto_atendimento": clas.str.contains("PRONTO ATENDIMENTO", regex=False).astype("int32"),
        "qtd_laboratorios": clas.str.contains("LABORATORIO", regex=False).astype("int32"),
        "qtd_consultorios": tipo.str.contains("CONSULTORIO", regex=False).astype("int32"),
    }).dropna(subset=["competencia", "id_produto_saude", "id_municipio"])
    if flags.empty:
        return pd.DataFrame()
    return flags.groupby(list(REDE_KEYS), as_index=False)[list(REDE_QTD_COLS)].sum()


def finalize_rede_credenciada(agg: pd.DataFrame) -> pd.DataFrame:
    """`competencia` (YYYYMM) vira o id da série mensal, como em market_share."""
    if agg.empty:
        return agg
    out = agg.copy()
    out["id_serie_mensal"] = out["competencia"].astype("int64")
    out["fonte_estimativa"] = REDE_FONTE
    out["id"] = stable_id(out, ["id_produto_saude", "id_municipio", "id_serie_mensal"])
    return out


# ---------------------------------------------------------------------------
# MARKET_SHARE  (dados_de_beneficiarios_por_operadora/sib_ativo_[UF].zip)
# instrucoes.md: COUNT de beneficiários agrupado por município e operadora.
# percentual_market_share = beneficiários da operadora / total do município.
# ---------------------------------------------------------------------------

SIB_COLS = ("ID_TEMPO_COMPETENCIA", "REGISTRO_OPERADORA", "CD_MUNICIPIO")


def transform_sib_chunk(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "competencia": _int(_str(df["ID_TEMPO_COMPETENCIA"]).str.replace("-", "", regex=False)),
        "id_operadora": _int(df["REGISTRO_OPERADORA"]),
        "id_municipio": _int(df["CD_MUNICIPIO"]),
    }).dropna()
    out["total_beneficiarios"] = 1
    return out


def finalize_market_share(agg: pd.DataFrame) -> pd.DataFrame:
    """`competencia` (YYYYMM) vem do próprio arquivo e vira o id da série mensal."""
    if agg.empty:
        return pd.DataFrame()
    out = agg.copy()
    total_mun = out.groupby(["competencia", "id_municipio"])["total_beneficiarios"].transform("sum")
    out["percentual_market_share"] = (out["total_beneficiarios"] / total_mun).round(4)
    out["id_serie_mensal"] = out["competencia"].astype("int64")
    out["id"] = stable_id(out, ["id_operadora", "id_municipio", "id_serie_mensal"])
    return out


# ---------------------------------------------------------------------------
# PERFIL_SAUDE_PUBLICA.taxa_cobertura_plano_saude  (pda-047-taxa_cobertura.csv)
# O arquivo é por município/ano (aberto por sexo e faixa etária), sem
# operadora -> não cabe em market_share; a coluna equivalente no DDL é
# perfil_saude_publica.taxa_cobertura_plano_saude (fração 0..1, NUMERIC(5,4)).
# ---------------------------------------------------------------------------

TAXA_COBERTURA_COLS = ("PERIODO", "CD_MUNICIPIO", "BENEF_TOTAL", "POPULACAO")


def transform_taxa_cobertura_chunk(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "ano": _int(df["PERIODO"]),
        "id_municipio": _int(df["CD_MUNICIPIO"]),
        "benef": _int(df["BENEF_TOTAL"]).fillna(0),
        "populacao": _int(df["POPULACAO"]).fillna(0),
    }).dropna(subset=["ano", "id_municipio"])


def finalize_perfil_saude_publica(agg: pd.DataFrame, pop_ibge: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """pop_ibge (id_municipio, ano, populacao) preenche a população que o PDA 047
    publica zerada; só é usada para o MESMO ano, sem misturar períodos."""
    if agg.empty:
        return pd.DataFrame()
    agg = agg.copy()
    if pop_ibge is not None and not pop_ibge.empty:
        merged = agg.merge(pop_ibge.rename(columns={"populacao": "pop_ibge"}),
                           on=["id_municipio", "ano"], how="left")
        merged["populacao"] = merged["populacao"].where(merged["populacao"] > 0, merged["pop_ibge"].fillna(0))
        agg = merged.drop(columns="pop_ibge")
    pop = agg["populacao"].astype("Float64")
    taxa = (agg["benef"] / pop.where(pop > 0)).round(4)
    out = pd.DataFrame({
        "id_municipio": agg["id_municipio"],
        "id_serie_anual": agg["ano"],
        "populacao_estimada": agg["populacao"].where(agg["populacao"] > 0),
        "taxa_cobertura_plano_saude": taxa.where(taxa < 10),  # limite NUMERIC(5,4)
    })
    out["id"] = stable_id(out, ["id_municipio", "id_serie_anual"])
    return out


# ---------------------------------------------------------------------------
# IBGE — API de localidades e SIDRA 6579 (população residente estimada)
# Documentação do cliente: "códigos e nomes territoriais são obtidos do IBGE".
# municipio.id continua sendo o código de 6 dígitos usado pela ANS (chave de
# junção de todos os arquivos); codigo_ibge recebe o código oficial de 7 dígitos.
# ---------------------------------------------------------------------------

def transform_ibge_estados(data: list[dict]) -> pd.DataFrame:
    return pd.DataFrame({
        "id": [int(e["id"]) for e in data],
        "codigo_ibge": [str(e["id"]) for e in data],
        "sigla": [e["sigla"] for e in data],
        "nome": [e["nome"] for e in data],
        "regiao": [e["regiao"]["nome"] for e in data],
    })


def _uf_id(m: dict) -> Optional[int]:
    micro = m.get("microrregiao") or {}
    uf = (micro.get("mesorregiao") or {}).get("UF") or \
        ((m.get("regiao-imediata") or {}).get("regiao-intermediaria") or {}).get("UF") or {}
    return uf.get("id")


def transform_ibge_municipios(data: list[dict]) -> pd.DataFrame:
    return pd.DataFrame({
        "id": [int(m["id"]) // 10 for m in data],
        "codigo_ibge": [str(m["id"]) for m in data],
        "nome": [m["nome"] for m in data],
        "id_estado": [_uf_id(m) for m in data],
    })


def transform_sidra_populacao(data: list[dict]) -> pd.DataFrame:
    """Resposta do agregado 6579 -> (id_municipio 6 dígitos, ano, populacao)."""
    rows = []
    for serie in data[0]["resultados"][0]["series"]:
        cod = int(serie["localidade"]["id"])
        for ano, valor in serie["serie"].items():
            if str(valor).isdigit():
                rows.append((cod // 10, int(ano), int(valor)))
    return pd.DataFrame(rows, columns=["id_municipio", "ano", "populacao"])


def finalize_populacao_anual(pop: pd.DataFrame) -> pd.DataFrame:
    """Série histórica do SIDRA 6579 -> perfil_saude_publica.populacao_estimada
    por (município, ano). Mesmo id de finalize_perfil_saude_publica, então a
    taxa de cobertura e a população do mesmo ano caem na mesma linha."""
    if pop.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "id_municipio": pop["id_municipio"].astype("int64"),
        "id_serie_anual": pop["ano"].astype("int64"),
        "populacao_estimada": pop["populacao"].astype("int64"),
    })
    out["id"] = stable_id(out, ["id_municipio", "id_serie_anual"])
    return out


def populacao_vigente(pop: pd.DataFrame) -> pd.DataFrame:
    """Último ano publicado de cada município -> municipio.populacao_estimada
    + id_serie_anual (relação "vigência" do DER)."""
    ultimo = pop.sort_values("ano").drop_duplicates("id_municipio", keep="last")
    return pd.DataFrame({
        "id": ultimo["id_municipio"].astype("int64"),
        "populacao_estimada": ultimo["populacao"].astype("int64"),
        "id_serie_anual": ultimo["ano"].astype("int64"),
    })


# ---------------------------------------------------------------------------
# PRODUTO_SAUDE.nota_sinistralidade_media  (painel_precificacao-031)
# instrucoes.md: derivado da NT do PDA 031 cruzando PCT_DESP_ASS
# (percentual da despesa assistencial na nota técnica) -> média por plano.
# ---------------------------------------------------------------------------

PAINEL_COLS = ("ID_PLANO", "PCT_DESP_ASS")


def transform_painel_chunk(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"id": _int(df["ID_PLANO"]), "soma": _decimal(df["PCT_DESP_ASS"])}).dropna()
    out["n"] = 1
    return out


def finalize_sinistralidade(agg: pd.DataFrame) -> pd.DataFrame:
    if agg.empty:
        return pd.DataFrame()
    return pd.DataFrame({"id": agg["id"], "nota_sinistralidade_media": (agg["soma"] / agg["n"]).round(4)})
