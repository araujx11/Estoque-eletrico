"""Registro de auditoria. As funções só adicionam à sessão: quem chama faz o commit, assim o log
entra na MESMA transação da ação (ou ambos acontecem, ou nenhum)."""
from app.models import LogAuditoria

_SENSIVEIS = {"senha", "senha_hash", "nova_senha", "password", "token", "access_token", "authorization"}


def _limpar(valor):
    """Remove (recursivamente) qualquer chave que possa carregar segredo."""
    if isinstance(valor, dict):
        return {k: _limpar(v) for k, v in valor.items() if str(k).lower() not in _SENSIVEIS}
    if isinstance(valor, (list, tuple)):
        return [_limpar(v) for v in valor]
    return valor


def registrar(db, usuario_id: int | None, acao: str, entidade: str | None = None,
              entidade_id=None, detalhes: dict | None = None) -> None:
    db.add(LogAuditoria(
        usuario_id=usuario_id, acao=acao, entidade=entidade,
        entidade_id=None if entidade_id is None else str(entidade_id),
        detalhes=_limpar(detalhes) if detalhes else None,
    ))
