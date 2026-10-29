"""Exportação CSV compatível com o Excel em português: separador ';', UTF-8 com BOM, decimal com vírgula."""
import csv
import io
from datetime import date, datetime

from fastapi.responses import Response

_PERIGOSOS = ("=", "+", "-", "@", "\t", "\r")


def _celula(valor, tipo: str) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y" if tipo == "data" else "%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float):
        return f"{valor:.3f}".rstrip("0").rstrip(".").replace(".", ",") if tipo != "percentual" else f"{valor:.1f}".replace(".", ",")
    texto = str(valor)
    # evita "injeção de fórmula" ao abrir no Excel (nomes vêm de usuários)
    if tipo == "texto" and texto.startswith(_PERIGOSOS):
        texto = "'" + texto
    return texto


def resposta_csv(nome_arquivo: str, colunas: list[dict], linhas: list[dict]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow([c["titulo"] for c in colunas])
    for linha in linhas:
        w.writerow([_celula(linha.get(c["chave"]), c.get("tipo", "texto")) for c in colunas])
    return Response(
        content=("\ufeff" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}.csv"'},
    )
