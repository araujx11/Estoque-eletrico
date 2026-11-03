"""
Mensagens de erro em português (Sprint 05): validação 422 traduzida, sem eco do valor enviado, e textos padrão do framework.
"""
import re

import pytest

from app.core.erros import rotulo, traduzir

INGLES = re.compile(r"\b(should|Input|Field required|String|greater|less|valid|at least|at most)\b")


def _detalhes(r):
    assert r.status_code == 422
    return r.json()["detail"]


# ---------- unidade: tradução e rótulo ----------

@pytest.mark.parametrize("erro,esperado", [
    ({"type": "missing"}, "é obrigatório"),
    ({"type": "string_too_short", "ctx": {"min_length": 8}}, "deve ter pelo menos 8 caractere(s)"),
    ({"type": "string_too_long", "ctx": {"max_length": 120}}, "deve ter no máximo 120 caracteres"),
    ({"type": "greater_than_equal", "ctx": {"ge": 0}}, "deve ser maior ou igual a 0"),
    ({"type": "greater_than", "ctx": {"gt": 0}}, "deve ser maior que 0"),
    ({"type": "less_than_equal", "ctx": {"le": 5}}, "deve ser menor ou igual a 5"),
    ({"type": "float_parsing"}, "deve ser um número"),
    ({"type": "int_parsing"}, "deve ser um número"),
    ({"type": "enum", "ctx": {"expected": "'gestor', 'almoxarife' or 'engenheiro'"}}, "valor inválido; use um destes: gestor, almoxarife ou engenheiro"),
    ({"type": "string_pattern_mismatch"}, "formato inválido"),
    ({"type": "too_short", "ctx": {"min_length": 1}}, "deve ter pelo menos 1 item(ns)"),
    ({"type": "value_error", "msg": "Value error, O ajuste não pode ser zero"}, "O ajuste não pode ser zero"),
    ({"type": "tipo_desconhecido", "msg": "texto original"}, "texto original"),
])
def test_traduzir(erro, esperado):
    assert traduzir(erro) == esperado


@pytest.mark.parametrize("loc,esperado", [
    (("body", "quantidade_estoque"), "Quantidade em estoque"),
    (("body", "itens", 0, "quantidade_solicitada"), "Quantidade solicitada (item 1)"),
    (("body", "itens", 2, "material_id"), "Material (item 3)"),
    (("query", "tamanho"), "Tamanho da página"),
    (("body", "campo_novo_sem_rotulo"), "Campo novo sem rotulo"),
    (("body",), "Dado informado"),
])
def test_rotulo(loc, esperado):
    assert rotulo(loc) == esperado


# ---------- API: mensagens reais ----------

async def test_nome_vazio_em_material(client, gestor):
    d = _detalhes(await client.post("/materiais", headers=gestor, json={"nome": "", "unidade": "m", "quantidade_estoque": 1}))
    assert d[0]["campo"] == "Nome" and d[0]["msg"] == "deve ter pelo menos 1 caractere(s)"


async def test_quantidade_negativa_em_material(client, gestor):
    d = _detalhes(await client.post("/materiais", headers=gestor, json={"nome": "X", "unidade": "m", "quantidade_estoque": -5}))
    assert (d[0]["campo"], d[0]["msg"]) == ("Quantidade em estoque", "deve ser maior ou igual a 0")


async def test_unidade_invalida_lista_as_opcoes(client, gestor):
    d = _detalhes(await client.post("/materiais", headers=gestor, json={"nome": "X", "unidade": "xyz", "quantidade_estoque": 1}))
    assert d[0]["campo"] == "Unidade" and d[0]["msg"].startswith("valor inválido; use um destes: m, un, cx")


async def test_quantidade_nao_numerica(client, gestor):
    d = _detalhes(await client.post("/materiais", headers=gestor, json={"nome": "X", "unidade": "m", "quantidade_estoque": "muito"}))
    assert (d[0]["campo"], d[0]["msg"]) == ("Quantidade em estoque", "deve ser um número")


async def test_campo_obrigatorio_ausente(client, gestor):
    d = _detalhes(await client.post("/materiais", headers=gestor, json={"unidade": "m", "quantidade_estoque": 1}))
    assert (d[0]["campo"], d[0]["msg"]) == ("Nome", "é obrigatório")


async def test_erros_de_varios_campos_vem_juntos(client, gestor):
    d = _detalhes(await client.post("/materiais", headers=gestor, json={"nome": "", "unidade": "xyz", "quantidade_estoque": -1}))
    assert {e["campo"] for e in d} == {"Nome", "Unidade", "Quantidade em estoque"}


async def test_item_do_pedido_indica_qual_item(client, gestor, dados):
    d = _detalhes(await client.post("/pedidos", headers=gestor, json={
        "obra_id": dados["obra_a"]["id"],
        "itens": [{"material_id": dados["material"]["id"], "quantidade_solicitada": 1},
                  {"material_id": dados["material"]["id"], "quantidade_solicitada": 0}]}))
    assert d[0]["campo"] == "Quantidade solicitada (item 2)" and d[0]["msg"] == "deve ser maior que 0"


async def test_pedido_sem_itens(client, gestor, dados):
    d = _detalhes(await client.post("/pedidos", headers=gestor, json={"obra_id": dados["obra_a"]["id"], "itens": []}))
    assert (d[0]["campo"], d[0]["msg"]) == ("Itens do pedido", "deve ter pelo menos 1 item(ns)")


async def test_validador_proprio_sem_prefixo_em_ingles(client, gestor, dados):
    d = _detalhes(await client.post("/movimentacoes/ajuste", headers=gestor,
                                    json={"material_id": dados["material"]["id"], "quantidade": 0, "observacao": "teste de zero"}))
    assert d[0]["msg"] == "O ajuste não pode ser zero"


async def test_parametro_de_consulta_invalido(client, gestor):
    d = _detalhes(await client.get("/relatorios/consumo", headers=gestor, params={"tamanho": 0}))
    assert d[0]["campo"] == "Tamanho da página" and d[0]["msg"] == "deve ser maior ou igual a 1"


async def test_senha_curta_nao_volta_na_resposta(client, gestor):
    """Antes, a resposta 422 ecoava o valor enviado (`input`): a senha digitada voltava em texto claro."""
    r = await client.post("/usuarios", headers=gestor, json={"nome": "X", "email": "x@teste.com", "senha": "abc123", "perfil": "gestor"})
    assert r.status_code == 422
    assert "abc123" not in r.text and "input" not in r.json()["detail"][0] and "ctx" not in r.json()["detail"][0]
    assert r.json()["detail"][0]["campo"] == "Senha"


async def test_nenhuma_mensagem_de_validacao_fica_em_ingles(client, gestor):
    r = await client.post("/materiais", headers=gestor, json={"nome": "", "unidade": "xyz", "quantidade_estoque": "x", "estoque_critico": -1})
    for e in _detalhes(r):
        assert not INGLES.search(e["msg"]), e


async def test_sem_login_a_mensagem_esta_em_portugues_e_mantem_o_cabecalho(client):
    r = await client.get("/usuarios")
    assert r.status_code == 401
    assert r.json()["detail"] == "É preciso entrar no sistema para acessar este recurso."
    assert r.headers.get("www-authenticate") == "Bearer"


async def test_rota_inexistente(client):
    r = await client.get("/nao-existe")
    assert r.status_code == 404 and r.json()["detail"] == "Recurso não encontrado."


async def test_mensagens_das_rotas_continuam_iguais(client, gestor):
    """Os detail já escritos em português pelas rotas não podem ser alterados pelo tratador."""
    r = await client.post("/materiais", headers=gestor, json={"nome": "Cabo", "unidade": "m", "quantidade_estoque": 1})
    r = await client.post("/materiais", headers=gestor, json={"nome": "Cabo", "unidade": "m", "quantidade_estoque": 1})
    assert r.status_code == 409 and r.json()["detail"] == "Já existe um material com esse nome"
