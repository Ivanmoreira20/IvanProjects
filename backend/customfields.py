from __future__ import annotations

import re
from datetime import date
from typing import Any

import db

MAX_FIELDS_PER_ENTITY = 40
MAX_TEXT = 500
MAX_OPTIONS = 40

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")

VERDADEIROS = frozenset({"1", "true", "sim", "yes", "on", "verdadeiro"})

def slugify(label: str) -> str:
    base = _NAO_ALFANUM.sub("_", db.deburr(label)).strip("_")
    return (base or "campo")[:40]

def field_to_dict(row: db.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "entity": row["entity"],
        "key": row["key"],
        "label": row["label"],
        "type": row["type"],
        "options": db.json_load(row["options"], []),
        "description": row["description"],
        "required": bool(row["required"]),
        "position": row["position"],
        "active": bool(row["active"]),
    }

def list_fields(
    conn: db.Connection, user_id: int, entity: str = "lead", *, only_active: bool = False
) -> list[dict]:
    sql = "SELECT * FROM custom_fields WHERE user_id = ? AND entity = ?"
    if only_active:
        sql += " AND active = 1"
    sql += " ORDER BY position, id"
    return [field_to_dict(linha) for linha in conn.execute(sql, (user_id, entity))]

def unique_key(conn: db.Connection, user_id: int, entity: str, label: str) -> str:
    base = slugify(label)
    existentes = {
        linha["key"]
        for linha in conn.execute(
            "SELECT key FROM custom_fields WHERE user_id = ? AND entity = ?", (user_id, entity)
        )
    }
    if base not in existentes:
        return base
    for sufixo in range(2, 100):
        candidato = f"{base}_{sufixo}"
        if candidato not in existentes:
            return candidato
    raise ValueError("não foi possível gerar uma chave única para este campo")

def count_fields(conn: db.Connection, user_id: int, entity: str) -> int:
    linha = conn.execute(
        "SELECT COUNT(*) AS t FROM custom_fields WHERE user_id = ? AND entity = ?",
        (user_id, entity),
    ).fetchone()
    return int(linha["t"])

def _numero(valor: Any, rotulo: str) -> float:
    texto = str(valor).strip().replace(".", "").replace(",", ".") if isinstance(valor, str) else valor
    try:
        return float(texto)
    except (TypeError, ValueError):
        raise ValueError(f"“{rotulo}” precisa ser um número") from None

def _data(valor: Any, rotulo: str) -> tuple[str, float]:
    texto = str(valor or "").strip()[:10]
    try:
        dia = date.fromisoformat(texto)
    except ValueError:
        raise ValueError(f"“{rotulo}” precisa ser uma data no formato AAAA-MM-DD") from None

    return dia.isoformat(), float(dia.year * 10000 + dia.month * 100 + dia.day)

def coerce(campo: dict, valor: Any) -> tuple[str, float | None]:
    tipo = campo["type"]
    rotulo = campo["label"]
    opcoes = campo.get("options") or []

    if valor is None:
        return "", None

    if tipo == "sim_nao":
        marcado = valor is True or str(valor).strip().lower() in VERDADEIROS
        return ("sim" if marcado else "nao"), (1.0 if marcado else 0.0)

    if tipo in ("numero", "moeda"):
        texto = str(valor).strip()
        if not texto:
            return "", None
        numero = _numero(valor, rotulo)
        return (f"{numero:g}", numero)

    if tipo == "data":
        if not str(valor).strip():
            return "", None
        return _data(valor, rotulo)

    if tipo == "lista":
        texto = str(valor).strip()
        if not texto:
            return "", None
        if texto not in opcoes:
            raise ValueError(f"“{rotulo}”: escolha uma das opções cadastradas")
        return texto, None

    if tipo == "multipla":
        escolhas = valor if isinstance(valor, list) else [p for p in str(valor).split("|") if p]
        limpos = [str(e).strip() for e in escolhas if str(e).strip()]
        invalidos = [e for e in limpos if e not in opcoes]
        if invalidos:
            raise ValueError(f"“{rotulo}”: opção inválida — {', '.join(invalidos[:3])}")
        return "|".join(limpos[:MAX_OPTIONS]), None

    texto = str(valor).strip()[:MAX_TEXT]
    if tipo == "email" and texto and not _EMAIL_RE.match(texto):
        raise ValueError(f"“{rotulo}” precisa ser um e-mail válido")
    if tipo == "telefone" and texto and len(re.sub(r"\D", "", texto)) < 8:
        raise ValueError(f"“{rotulo}” precisa ser um telefone válido")
    return texto, None

def set_values(
    conn: db.Connection,
    user_id: int,
    entity: str,
    entity_id: int,
    valores: dict[str, Any],
    *,
    exigir_obrigatorios: bool = True,
) -> None:
    campos = {c["key"]: c for c in list_fields(conn, user_id, entity, only_active=True)}
    if not campos:
        return

    agora = db.now_iso()
    faltando: list[str] = []

    for chave, campo in campos.items():
        if chave not in valores:
            if exigir_obrigatorios and campo["required"]:
                atual = conn.execute(
                    """SELECT value_text FROM custom_values
                        WHERE field_id = ? AND entity = ? AND entity_id = ?""",
                    (campo["id"], entity, entity_id),
                ).fetchone()
                if atual is None or not atual["value_text"]:
                    faltando.append(campo["label"])
            continue

        texto, numero = coerce(campo, valores[chave])
        if exigir_obrigatorios and campo["required"] and not texto:
            faltando.append(campo["label"])
            continue

        if not texto and numero is None:
            conn.execute(
                "DELETE FROM custom_values WHERE field_id = ? AND entity = ? AND entity_id = ?",
                (campo["id"], entity, entity_id),
            )
            continue

        conn.execute(
            """INSERT INTO custom_values
                   (field_id, user_id, entity, entity_id, value_text, value_num, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(field_id, entity, entity_id) DO UPDATE SET
                   value_text = excluded.value_text,
                   value_num  = excluded.value_num,
                   updated_at = excluded.updated_at""",
            (campo["id"], user_id, entity, entity_id, texto, numero, agora),
        )

    if faltando:
        raise ValueError("Preencha os campos obrigatórios: " + ", ".join(faltando))

def values_for(conn: db.Connection, user_id: int, entity: str, entity_id: int) -> dict[str, Any]:
    linhas = conn.execute(
        """SELECT f.key, f.type, v.value_text
             FROM custom_values v
             JOIN custom_fields f ON f.id = v.field_id
            WHERE v.user_id = ? AND v.entity = ? AND v.entity_id = ?""",
        (user_id, entity, entity_id),
    ).fetchall()
    saida: dict[str, Any] = {}
    for linha in linhas:
        if linha["type"] == "multipla":
            saida[linha["key"]] = [p for p in linha["value_text"].split("|") if p]
        elif linha["type"] == "sim_nao":
            saida[linha["key"]] = linha["value_text"] == "sim"
        else:
            saida[linha["key"]] = linha["value_text"]
    return saida

def values_for_many(
    conn: db.Connection, user_id: int, entity: str, ids: list[int]
) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    marcadores = ", ".join("?" for _ in ids)
    linhas = conn.execute(
        f"""SELECT v.entity_id, f.key, f.type, v.value_text
              FROM custom_values v
              JOIN custom_fields f ON f.id = v.field_id
             WHERE v.user_id = ? AND v.entity = ? AND v.entity_id IN ({marcadores})""",
        (user_id, entity, *ids),
    ).fetchall()
    saida: dict[int, dict[str, Any]] = {}
    for linha in linhas:
        alvo = saida.setdefault(int(linha["entity_id"]), {})
        if linha["type"] == "multipla":
            alvo[linha["key"]] = [p for p in linha["value_text"].split("|") if p]
        elif linha["type"] == "sim_nao":
            alvo[linha["key"]] = linha["value_text"] == "sim"
        else:
            alvo[linha["key"]] = linha["value_text"]
    return saida

def delete_values(conn: db.Connection, user_id: int, entity: str, entity_id: int) -> None:
    conn.execute(
        "DELETE FROM custom_values WHERE user_id = ? AND entity = ? AND entity_id = ?",
        (user_id, entity, entity_id),
    )
