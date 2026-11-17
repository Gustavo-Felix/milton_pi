"""
FASE 03/04 — Download dos arquivos ANS (HTTP) e leitura em chunks.

Regras do Orquestrador:
- Arquivos ZIP devem ser baixados e processados pela pipeline.
- Não assumir que o conteúdo do ZIP cabe em memória.
- Ler o CSV diretamente do ZIP em chunks (sem extrair para disco).
- Chunks de 1000 registros.

Estratégia:
1. Listar o diretório ANS e selecionar os arquivos pelo regex do config.json.
2. Baixar um arquivo por vez (stream) para um diretório temporário.
3. Para cada CSV (o próprio arquivo ou cada membro do ZIP) entregar um
   iterador de chunks lendo apenas as colunas necessárias.
4. O diretório temporário é apagado assim que o arquivo é consumido.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile64.zipfile as zipfile
import zipfile as _stdlib_zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Iterator, Optional
from urllib.parse import unquote, urljoin

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import zipfile64.support as _zipfile64_support

logger = logging.getLogger(__name__)

CHUNK_SIZE = int(os.getenv("PIPELINE_CHUNK_SIZE", "1000"))  # regra do orquestrador
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 120
STREAM_BLOCK = 1024 * 1024  # 1 MB
SAMPLE_BYTES = 64 * 1024
PROGRESS_ROWS = 1_000_000  # linhas entre logs de progresso

_ENCODINGS = ("utf-8", "cp1252", "latin1")
_SEPARATORS = (";", ",", "\t", "|")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_COMPETENCIA_RE = re.compile(r"(\d{6})")

# (nome do CSV, iterador de chunks)
CsvSource = tuple[str, Iterator[pd.DataFrame]]


class _Deflate64Python314Adapter:
    """Avoid truncated Deflate64 output with Python 3.14's bounded ZIP reads.

    inflate64 does not expose a ``needs_input`` flag. Python's ZIP reader uses
    that flag to drain buffered output before reading more compressed bytes.
    Decode each bounded input block completely so no output remains buffered.
    """

    def __init__(self) -> None:
        self._decompressor = _zipfile64_support.Deflate64Decompressor()

    @property
    def eof(self) -> bool:
        return self._decompressor.eof

    @property
    def needs_input(self) -> bool:
        return True

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        return self._decompressor.decompress(data, max_length=0)

    def flush(self) -> bytes:
        return self._decompressor.flush()


_zipfile_get_decompressor = _stdlib_zipfile._get_decompressor


def _get_decompressor(compress_type: int):
    if compress_type == 9:
        return _Deflate64Python314Adapter()
    return _zipfile_get_decompressor(compress_type)


_stdlib_zipfile._get_decompressor = _get_decompressor


def build_session(retries: int = 3, backoff: float = 1.0) -> requests.Session:
    """Session HTTP com retry em erros transientes. Use com `with`."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# Descoberta de arquivos
# ---------------------------------------------------------------------------

def list_files(session: requests.Session, dir_url: str) -> list[str]:
    """Lista os nomes de arquivo (não diretórios) de um índice HTTP da ANS."""
    with session.get(dir_url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as resp:
        resp.raise_for_status()
        html = resp.text
    names = []
    for href in _HREF_RE.findall(html):
        if href.startswith(("?", "/", "#")) or href.endswith("/") or "://" in href:
            continue
        names.append(unquote(href))
    return names


def select_files(
    names: Iterable[str],
    pattern: Optional[str],
    mode: str = "primeiro",
    competencia: Optional[int] = None,
) -> list[str]:
    """Filtra `names` pelo regex e aplica o modo de seleção.

    - primeiro: o primeiro arquivo que bate.
    - todos: todos os arquivos que batem.
    - competencia: o arquivo com YYYYMM mais recente <= `competencia`
      (ou o mais recente de todos, se nenhum for anterior).
    """
    regex = re.compile(pattern) if pattern else re.compile(r"\.(csv|zip)$", re.IGNORECASE)
    matched = [n for n in names if regex.search(n)]
    if not matched:
        return []
    if mode == "todos":
        return sorted(matched)
    if mode == "competencia":
        dated = sorted(
            (int(m.group(1)), n) for n in matched if (m := _COMPETENCIA_RE.search(n))
        )
        if not dated:
            return []
        eligible = [n for yyyymm, n in dated if competencia is None or yyyymm <= competencia]
        return [eligible[-1] if eligible else dated[-1][1]]
    return [matched[0]]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(session: requests.Session, url: str, dest_dir: str) -> Path:
    """Faz stream de `url` para `dest_dir`. Nunca carrega o arquivo na RAM."""
    dest = Path(dest_dir) / Path(unquote(url.rsplit("/", 1)[-1])).name
    logger.info("Download: %s", url)
    downloaded = 0
    with session.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as resp:
        resp.raise_for_status()
        expected = resp.headers.get("Content-Length")
        content_encoding = resp.headers.get("Content-Encoding", "identity").lower()
        with dest.open("wb") as fh:
            for block in resp.iter_content(chunk_size=STREAM_BLOCK):
                fh.write(block)
                downloaded += len(block)
    if expected and content_encoding in ("", "identity") and downloaded != int(expected):
        dest.unlink(missing_ok=True)
        raise IOError(
            f"Download incompleto de {dest.name}: {downloaded} de {expected} bytes"
        )
    logger.info("Download concluído: %s (%.1f MB)", dest.name, downloaded / 1024 / 1024)
    return dest


def is_bad_zip_crc_error(exc: BaseException) -> bool:
    """Identifica corrupção de membro ZIP, que pode ser transitória no download."""
    return (
        type(exc).__name__ == "BadZipFile"
        and str(exc).startswith("Bad CRC-32 for file ")
    )


# ---------------------------------------------------------------------------
# Leitura de CSV em chunks
# ---------------------------------------------------------------------------

def detect_csv_params(sample: bytes) -> tuple[str, str]:
    """Detecta encoding e separador a partir dos primeiros bytes do arquivo.

    A decodificação é estrita (sem `errors="replace"`), por isso um arquivo
    latin1 não é mais classificado como utf-8.
    """
    # Corta na última quebra de linha para não partir um caractere multibyte
    cut = sample.rfind(b"\n")
    if cut > 0:
        sample = sample[:cut]
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", _detect_sep(sample[3:].decode("utf-8", errors="ignore"))
    for enc in _ENCODINGS:
        try:
            text = sample.decode(enc)
        except UnicodeDecodeError:
            continue
        return enc, _detect_sep(text)
    return "latin1", ";"


def _detect_sep(text: str) -> str:
    header = text.split("\n", 1)[0]
    return max(_SEPARATORS, key=header.count)


def _read_chunks(
    opener: Callable[[], BinaryIO],
    name: str,
    usecols: Optional[Iterable[str]],
    chunk_size: int,
) -> Iterator[pd.DataFrame]:
    with opener() as fh:
        enc, sep = detect_csv_params(fh.read(SAMPLE_BYTES))
    logger.info("CSV %s: encoding=%s sep=%r", name, enc, sep)

    wanted = set(usecols) if usecols else None
    with opener() as fh, pd.read_csv(
        fh,
        sep=sep,
        encoding=enc,
        dtype=str,
        usecols=(lambda c: c.strip() in wanted) if wanted else None,
        chunksize=chunk_size,
        on_bad_lines="warn",
        encoding_errors="replace",
        low_memory=True,
    ) as reader:
        rows = 0
        next_log = PROGRESS_ROWS
        for chunk in reader:
            chunk.columns = chunk.columns.str.strip()
            rows += len(chunk)
            if rows >= next_log:
                logger.info("CSV %s: %d linhas lidas", name, rows)
                next_log += PROGRESS_ROWS
            yield chunk
    logger.info("CSV %s: concluído (%d linhas)", name, rows)


def iter_csv_sources(
    file_path: Path,
    usecols: Optional[Iterable[str]] = None,
    chunk_size: int = CHUNK_SIZE,
) -> Iterator[CsvSource]:
    """Para cada CSV contido em `file_path` (CSV ou ZIP), entrega (nome, chunks).

    Membros de ZIP são lidos em streaming direto do arquivo compactado,
    sem extração para disco e sem carregar o conteúdo na RAM.
    """
    if not zipfile.is_zipfile(file_path):
        yield from _closing_source(
            file_path.name, _read_chunks(lambda: open(file_path, "rb"), file_path.name, usecols, chunk_size)
        )
        return

    with zipfile.ZipFile(file_path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not members:
            logger.warning("ZIP sem CSV: %s", file_path.name)
        for member in members:
            yield from _closing_source(
                member, _read_chunks(lambda m=member: zf.open(m), member, usecols, chunk_size)
            )


def _closing_source(name: str, chunks: Iterator[pd.DataFrame]) -> Iterator[CsvSource]:
    """Garante que o handle do CSV seja fechado mesmo se o consumidor abortar."""
    try:
        yield name, chunks
    finally:
        chunks.close()


@contextmanager
def _tempdir() -> Iterator[str]:
    with tempfile.TemporaryDirectory(prefix="ans_", ignore_cleanup_errors=True) as tmp:
        yield tmp


def iter_dataset_sources(
    session: requests.Session,
    base_url: str,
    ds_cfg: dict,
    competencia: Optional[int] = None,
    usecols: Optional[Iterable[str]] = None,
    chunk_size: int = CHUNK_SIZE,
) -> Iterator[CsvSource]:
    """Baixa os arquivos de um dataset (um por vez) e entrega cada CSV em chunks.

    Cada arquivo baixado é removido antes do download do próximo, então o uso
    de disco fica limitado ao maior arquivo do dataset.
    Levanta RuntimeError se nenhum arquivo for encontrado.
    """
    dir_url = urljoin(base_url, ds_cfg["path"])
    names = select_files(
        list_files(session, dir_url),
        ds_cfg.get("arquivo"),
        ds_cfg.get("selecao", "primeiro"),
        competencia,
    )
    if not names:
        raise RuntimeError(f"Nenhum arquivo encontrado em {dir_url} (padrão={ds_cfg.get('arquivo')})")

    for name in names:
        with _tempdir() as tmp:
            local = download(session, urljoin(dir_url, name), tmp)
            sources = iter_csv_sources(local, usecols=usecols, chunk_size=chunk_size)
            try:
                yield from sources
            finally:
                sources.close()  # fecha o ZIP antes de apagar o diretório
