from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import activities
import auth
import config
import db

logger = logging.getLogger("vertex.seed")

DEMO_CREDENTIALS = (
    ("Ana Torres", "ana@vertex.test", "Teste@1234"),
    ("Bruno Lima", "bruno@vertex.test", "Teste@5678"),
    ("Carla Mendes", "carla@vertex.test", "Teste@9012"),
)

ANA_LEADS = (
    ("Renata Albuquerque", "Clínica Vitalis", 68000.0, "Ganho", "Saúde", 5),
    ("Otávio Bittencourt", "Instituto Aprende Mais", 54000.0, "Ganho", "Educação", 4),
    ("Helena Vasconcelos", "Hospital São Lucas Diagnóstico", 47000.0, "Ganho", "Saúde", 3),
    ("Rodrigo Sampaio", "Colégio Horizonte Azul", 42000.0, "Proposta", "Educação", 2),
    ("Patrícia Quirino", "Laboratório Genoma Vida", 39000.0, "Negociação", "Saúde", 1),
    ("Fernando Meireles", "EduTech Cursos Livres", 33000.0, "Qualificação", "Educação", 0),
    ("Juliana Prates", "Rede Odonto Sorriso", 29000.0, "Prospecção", "Saúde", 0),
)

ANA_PERDAS = (
    ("Gustavo Nakahara", "Clínica Bem Estar Integrado", 51000.0, "Saúde", 3, "Preço"),
    ("Bianca Toledo", "Faculdade Novo Rumo", 36000.0, "Educação", 2, "Concorrente"),
    ("Sérgio Andrade", "Laboratório Precisão", 22000.0, "Saúde", 1, "Sem orçamento"),
    ("Camila Ferrari", "Escola de Idiomas Mundo", 18000.0, "Educação", 1, "Preço"),
    ("Wagner Peçanha", "Odonto Center Norte", 14000.0, "Saúde", 0, "Sem resposta"),
)

BRUNO_LEADS = (
    ("Marcelo Kuroda", "Nimbus Cloud Systems", 24000.0, "Prospecção", "SaaS", 3),
    ("Simone Delgado", "Metalúrgica Ferro Norte", 18000.0, "Prospecção", "Indústria", 2),
    ("Thiago Espinosa", "DataPulse Analytics", 15000.0, "Qualificação", "SaaS", 1),
    ("Kelly Onodera", "Indústria Plástica Verde", 11000.0, "Proposta", "Indústria", 0),
)

CARLA_LEADS: tuple = ()

def _months_ago(count: int) -> datetime:
    reference = db.utcnow()
    month = reference.month - count
    year = reference.year
    while month <= 0:
        month += 12
        year -= 1
    momento = datetime(year, month, 12, 10, 30, 0, tzinfo=timezone.utc)
    if momento > reference:
        momento = reference - timedelta(days=3)
    return momento

def _ensure_user(conn: db.Connection, name: str, email: str, password: str) -> int:
    row = conn.execute(
        "SELECT id, email_verified FROM users WHERE email = ?", (email,)
    ).fetchone()
    if row is not None:
        if int(row["email_verified"] or 0) == 0:
            conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (int(row["id"]),))
        return int(row["id"])
    cursor = conn.execute(
        """INSERT INTO users (email, name, password_hash, created_at,
                              email_verified, auth_provider)
           VALUES (?, ?, ?, ?, 1, 'password')""",
        (email, name, auth.hash_password(password), db.iso(_months_ago(6))),
    )
    return int(cursor.lastrowid)

def _ensure_leads(conn: db.Connection, user_id: int, rows: tuple) -> int:
    existing = conn.execute(
        "SELECT COUNT(*) AS total FROM leads WHERE user_id = ?", (user_id,)
    ).fetchone()
    if int(existing["total"]) > 0:
        return 0

    inserted = 0
    for name, company, value, status, segment, months_back in rows:
        moment = db.iso(_months_ago(months_back))
        fechado = moment if status in db.CLOSED_STATUSES else None
        cursor = conn.execute(
            """INSERT INTO leads (user_id, name, company, value, status, segment,
                                  closed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name, company, value, status, segment, fechado, moment, moment),
        )
        activities.log(
            conn, user_id, lead_id=int(cursor.lastrowid), kind="criacao",
            title="Lead criado", source="system", created_at=moment,
        )
        inserted += 1
    return inserted

def _ensure_losses(conn: db.Connection, user_id: int, rows: tuple) -> int:
    if not rows:
        return 0
    activities.ensure_loss_reasons(conn, user_id)
    inserted = 0
    for name, company, value, segment, months_back, motivo in rows:
        moment = db.iso(_months_ago(months_back))
        cursor = conn.execute(
            """INSERT INTO leads (user_id, name, company, value, status, segment,
                                  lost_reason, closed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'Perdido', ?, ?, ?, ?, ?)""",
            (user_id, name, company, value, segment, motivo, moment, moment, moment),
        )
        activities.log(
            conn, user_id, lead_id=int(cursor.lastrowid), kind="perda",
            title=f"Negócio perdido — {motivo}", source="system", created_at=moment,
        )
        inserted += 1
    return inserted

def seeding_enabled() -> bool:
    explicit = config.seed_flag()
    if explicit is not None:
        return explicit
    return True

def seed_demo_data() -> None:
    datasets = (ANA_LEADS, BRUNO_LEADS, CARLA_LEADS)
    perdas = (ANA_PERDAS, (), ())
    with db.get_conn() as conn:
        for (name, email, password), leads, perdidos in zip(DEMO_CREDENTIALS, datasets, perdas):
            user_id = _ensure_user(conn, name, email, password)
            created = _ensure_leads(conn, user_id, leads)

            if created:
                created += _ensure_losses(conn, user_id, perdidos)
            logger.info("Perfil %s (%s): %d lead(s) criado(s).", name, email, created)

def seed_if_empty() -> bool:
    if not seeding_enabled():
        logger.info(
            "Semeadura desligada (VERTEX_SEED=%s): nenhum perfil de demonstração "
            "será criado. Cadastre-se pela tela de registro.",
            config.get("VERTEX_SEED") or "não definida",
        )
        return False
    if db.count_users() > 0:
        return False
    seed_demo_data()
    return True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    db.init_db()
    seed_demo_data()
    print("Perfis de demonstração prontos:")
    for name, email, password in DEMO_CREDENTIALS:
        print(f"  {name:<14} {email:<20} senha: {password}")
