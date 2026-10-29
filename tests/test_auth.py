import jwt

from app.core import config


async def test_login_valido_retorna_token_e_usuario(client, gestor):
    r = (await client.post("/auth/login", data={"username": "gestor@teste.com", "password": "senha-do-gestor"}))
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["token_type"] == "bearer"
    assert corpo["usuario"]["perfil"] == "gestor"
    assert "senha_hash" not in corpo["usuario"]  # hash nunca é retornado
    assert "senha-do-gestor" not in r.text


async def test_login_email_e_case_insensitive(client, gestor):
    r = (await client.post("/auth/login", data={"username": " GESTOR@teste.com ", "password": "senha-do-gestor"}))
    assert r.status_code == 200


async def test_login_senha_errada(client, gestor):
    r = (await client.post("/auth/login", data={"username": "gestor@teste.com", "password": "errada"}))
    assert r.status_code == 401


async def test_login_email_inexistente(client, gestor):
    r = (await client.post("/auth/login", data={"username": "ninguem@teste.com", "password": "qualquer-coisa"}))
    assert r.status_code == 401


async def test_me_com_token(client, gestor):
    r = (await client.get("/auth/me", headers=gestor))
    assert r.status_code == 200
    assert r.json()["email"] == "gestor@teste.com"


async def test_token_expirado_ou_adulterado_da_401(client, gestor):
    expirado = jwt.encode({"sub": "1", "exp": 1}, config.SECRET_KEY, algorithm=config.ALGORITHM)
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {expirado}"})).status_code == 401
    falso = jwt.encode({"sub": "1"}, "outra-chave-qualquer-com-32-caracteres!!", algorithm="HS256")
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {falso}"})).status_code == 401


async def test_usuario_desativado_nao_loga_nem_usa_token_antigo(client, gestor):
    r = (await client.post("/usuarios", json={
        "nome": "Temp", "email": "temp@teste.com", "senha": "senha-12345", "perfil": "almoxarife",
    }, headers=gestor))
    uid = r.json()["id"]
    token = (await client.post("/auth/login", data={"username": "temp@teste.com", "password": "senha-12345"})).json()["access_token"]
    assert (await client.patch(f"/usuarios/{uid}", json={"ativo": False}, headers=gestor)).status_code == 200
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401
    assert (await client.post("/auth/login", data={"username": "temp@teste.com", "password": "senha-12345"})).status_code == 403


async def test_redefinir_senha(client, gestor):
    r = (await client.post("/usuarios", json={
        "nome": "Troca", "email": "troca@teste.com", "senha": "senha-antiga1", "perfil": "almoxarife",
    }, headers=gestor))
    uid = r.json()["id"]
    assert (await client.post(f"/usuarios/{uid}/redefinir-senha", json={"nova_senha": "senha-nova-123"}, headers=gestor)).status_code == 204
    assert (await client.post("/auth/login", data={"username": "troca@teste.com", "password": "senha-antiga1"})).status_code == 401
    assert (await client.post("/auth/login", data={"username": "troca@teste.com", "password": "senha-nova-123"})).status_code == 200


async def test_gestor_nao_pode_se_desativar(client, gestor):
    eu = (await client.get("/auth/me", headers=gestor)).json()
    assert (await client.patch(f"/usuarios/{eu['id']}", json={"ativo": False}, headers=gestor)).status_code == 409
    assert (await client.patch(f"/usuarios/{eu['id']}", json={"perfil": "almoxarife"}, headers=gestor)).status_code == 409


async def test_engenheiro_exige_obra(client, gestor):
    r = (await client.post("/usuarios", json={
        "nome": "Sem obra", "email": "semobra@teste.com", "senha": "senha-12345", "perfil": "engenheiro",
    }, headers=gestor))
    assert r.status_code == 422


async def test_email_duplicado(client, gestor):
    r = (await client.post("/usuarios", json={
        "nome": "Dup", "email": "gestor@teste.com", "senha": "senha-12345", "perfil": "almoxarife",
    }, headers=gestor))
    assert r.status_code == 409
