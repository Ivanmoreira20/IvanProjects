# Vertex CRM

CRM comercial multiusuário em português. Backend real: contas com senha,
sessões no servidor, isolamento por conta e dados que persistem. Frontend
estático servido pela própria API, na mesma origem (sem CORS).

**Stack:** FastAPI · `sqlite3` da biblioteca padrão (sem ORM) · uvicorn ·
frontend em HTML/CSS/JS puro.

## Funcionalidades

- **Clientes** — ficha única com histórico, busca, filtros e exportação CSV.
- **Negócios** — funil por etapa, com valor, dono e responsável.
- **Tarefas e acompanhamento** — próxima ação e prazo por negócio; o sistema
  aponta o que parou de andar.
- **Relatórios** — receita, ticket médio, conversão, valor no pipeline e motivo
  de cada perda, sempre sobre os dados reais da conta.
- **Propostas, Equipe (papéis Admin/Gestor/Vendedor), Automações e Inteligência
  comercial** — recursos do plano Pro.

Planos: Iniciante (R$ 39,99/mês) e Pro (R$ 79,99/mês); Empresa sob consulta.

## Como rodar

Pré-requisito: Python 3.11+.

```bash
cd Dashboard
python iniciar.py
```

O script cria o `.venv`, instala `backend/requirements.txt` e sobe o servidor em
**http://127.0.0.1:8000**. Na primeira execução o banco `backend/vertex.db` é
criado e três contas de demonstração são semeadas.

| Endereço | O que é |
|---|---|
| `/` | a aplicação |
| `/api` | a API |
| `/docs` | documentação interativa (OpenAPI) |

Contas de demonstração: `ana@vertex.test`, `bruno@vertex.test` e
`carla@vertex.test` (senhas em `backend/seed.py`). A Carla é proposital: não tem
nenhum lead, para provar que os gráficos ficam vazios em vez de inventar números.

## Segurança

- Senhas com `scrypt` (salt aleatório por usuário); comparação com
  `hmac.compare_digest`. Nenhuma resposta expõe hash ou senha.
- Sessões opacas (não JWT): o cookie carrega um token aleatório e o banco guarda
  só o SHA-256 dele — revogar é apagar uma linha.
- Cookies `HttpOnly`, `SameSite=Lax`, `Secure` sob HTTPS; CSRF por double-submit
  em toda escrita.
- Isolamento por conta: o identificador vem sempre da sessão, nunca do corpo ou
  da query; toda consulta é escopada. Acesso a recurso de outra conta responde
  `404`, não `403` (não confirma que o registro existe).
- Queries 100% parametrizadas; CSP restritiva sem CDN (todo asset é local);
  rate limit em login e cadastro.

## Testes

```bash
cd Dashboard
.venv/Scripts/python -m pytest backend/test_security.py -q
```

A suíte roda num banco temporário próprio e cobre isolamento entre contas, CSRF,
rate limit, anti-enumeração de e-mail, ciclo de vida da sessão e as regras de
plano e papel.

## Estrutura

```
Dashboard/
├── iniciar.py            # cria venv, instala deps e sobe o servidor
├── backend/              # FastAPI, sqlite3, auth, billing, testes
├── frontend/             # servido em / pela própria API
└── deploy/               # unidades systemd, nginx, backup e utilitários
```
