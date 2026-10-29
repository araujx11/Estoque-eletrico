"""
Configurações lidas de variáveis de ambiente (arquivo .env na raiz do projeto).

Os valores padrão existem só para desenvolvimento local: em qualquer outro
ambiente defina SECRET_KEY, ADMIN_EMAIL e ADMIN_SENHA no .env.
"""
import os

from dotenv import load_dotenv

load_dotenv()

SECRET_KEY_PADRAO = "dev-only-secret-key-troque-no-env-0123456789"

SECRET_KEY = os.getenv("SECRET_KEY", SECRET_KEY_PADRAO)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

# gestor criado automaticamente quando a tabela de usuários está vazia
ADMIN_NOME = os.getenv("ADMIN_NOME", "Administrador")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@estoque.local")
ADMIN_SENHA = os.getenv("ADMIN_SENHA", "admin12345")
