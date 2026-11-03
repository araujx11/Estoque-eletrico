"""
Mensagens de erro em português.

* Validação (HTTP 422): o framework devolve textos técnicos em inglês («Input should be greater than or equal to 0»)
  e ecoa o valor enviado (`input`), que pode ser uma senha. Aqui cada erro vira «Rótulo do campo: mensagem em português»,
  mantendo a forma `{"detail": [{"loc", "campo", "msg", "type"}]}` que o frontend já sabe exibir. O valor enviado NÃO é devolvido.
* Textos padrão do framework (401 «Not authenticated», 404 «Not Found»...): traduzidos; os `detail` já escritos em português
  pelas rotas passam sem alteração.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# nome técnico do campo -> rótulo mostrado ao usuário
ROTULOS = {
    "nome": "Nome", "email": "E-mail", "senha": "Senha", "nova_senha": "Nova senha", "perfil": "Perfil", "ativo": "Situação",
    "obra_id": "Obra", "material_id": "Material", "unidade": "Unidade", "cidade": "Cidade",
    "quantidade": "Quantidade", "quantidade_estoque": "Quantidade em estoque", "estoque_critico": "Estoque crítico",
    "quantidade_solicitada": "Quantidade solicitada", "itens": "Itens do pedido", "requisicoes": "Pedidos",
    "nivel_prioridade": "Prioridade", "prazo_entrega": "Prazo de entrega", "progresso": "Progresso",
    "descricao_andamento": "Descrição do andamento", "observacao": "Motivo / observação",
    "periodo": "Período", "pagina": "Página", "tamanho": "Tamanho da página", "formato": "Formato", "agrupar": "Agrupamento",
    "ordenar": "Ordenação", "busca": "Busca", "de": "Data inicial", "ate": "Data final",
}

TEXTOS_HTTP = {
    "Not authenticated": "É preciso entrar no sistema para acessar este recurso.",
    "Not Found": "Recurso não encontrado.",
    "Method Not Allowed": "Operação não permitida para este endereço.",
    "Forbidden": "Acesso negado.",
}


def _lista(valor) -> str:
    """«'a', 'b' or 'c'» (formato do pydantic) -> «a, b ou c»."""
    return str(valor).replace("'", "").replace(" or ", " ou ") if valor is not None else ""


def _num(v):
    """0.0 -> 0 (o pydantic devolve o limite de campos decimais como float)."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def rotulo(loc) -> str:
    """Rótulo do campo a partir do `loc` do pydantic, ex.: ('body', 'itens', 0, 'quantidade_solicitada') -> «Quantidade solicitada (item 1)»."""
    partes = [p for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    nome = next((p for p in reversed(partes) if isinstance(p, str)), "")
    texto = ROTULOS.get(nome, nome.replace("_", " ").capitalize() if nome else "Dado informado")
    indices = [p for p in partes if isinstance(p, int)]
    return f"{texto} (item {indices[-1] + 1})" if indices else texto


def traduzir(erro: dict) -> str:
    """Mensagem em português para um erro do pydantic (`erro['type']`, `erro['ctx']`, `erro['msg']`)."""
    tipo, ctx = erro.get("type", ""), erro.get("ctx") or {}
    if tipo == "missing":
        return "é obrigatório"
    if tipo == "string_too_short":
        return f"deve ter pelo menos {ctx.get('min_length')} caractere(s)"
    if tipo == "string_too_long":
        return f"deve ter no máximo {ctx.get('max_length')} caracteres"
    if tipo == "too_short":
        return f"deve ter pelo menos {ctx.get('min_length')} item(ns)"
    if tipo == "too_long":
        return f"deve ter no máximo {ctx.get('max_length')} item(ns)"
    if tipo == "greater_than_equal":
        return f"deve ser maior ou igual a {_num(ctx.get('ge'))}"
    if tipo == "greater_than":
        return f"deve ser maior que {_num(ctx.get('gt'))}"
    if tipo == "less_than_equal":
        return f"deve ser menor ou igual a {_num(ctx.get('le'))}"
    if tipo == "less_than":
        return f"deve ser menor que {_num(ctx.get('lt'))}"
    if tipo in ("float_parsing", "float_type", "int_parsing", "int_type", "int_from_float", "decimal_parsing", "decimal_type"):
        return "deve ser um número"
    if tipo in ("enum", "literal_error"):
        return f"valor inválido; use um destes: {_lista(ctx.get('expected'))}"
    if tipo == "string_pattern_mismatch":
        return "formato inválido"
    if tipo in ("string_type", "bytes_type"):
        return "deve ser um texto"
    if tipo in ("bool_parsing", "bool_type"):
        return "deve ser verdadeiro ou falso"
    if tipo.startswith("datetime") or tipo.startswith("date_"):
        return "data ou hora inválida"
    if tipo == "json_invalid":
        return "o conteúdo enviado não é um JSON válido"
    if tipo in ("list_type", "model_attributes_type", "dict_type"):
        return "formato de dado inválido"
    if tipo == "value_error":                       # validadores próprios, já em português: só tira o prefixo do pydantic
        return str(erro.get("msg", "")).removeprefix("Value error, ")
    return str(erro.get("msg", "valor inválido"))


async def _tratar_validacao(_: Request, exc: RequestValidationError) -> JSONResponse:
    detalhes = [{"loc": list(e.get("loc", [])), "campo": rotulo(e.get("loc", [])), "msg": traduzir(e), "type": e.get("type", "")}
                for e in exc.errors()]                # sem `input` e sem `ctx`: o valor enviado (ex.: senha) não volta na resposta
    return JSONResponse(status_code=422, content={"detail": detalhes})


async def _tratar_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detalhe = TEXTOS_HTTP.get(exc.detail, exc.detail) if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(status_code=exc.status_code, content={"detail": detalhe}, headers=getattr(exc, "headers", None))


def registrar_tratadores(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _tratar_validacao)
    app.add_exception_handler(StarletteHTTPException, _tratar_http)
