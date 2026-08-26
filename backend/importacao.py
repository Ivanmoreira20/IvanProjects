from __future__ import annotations

import csv
import io
import re
from typing import Any

import activities
import crm
import db
import intel

MAX_CSV_CHARS = 2_000_000
MAX_LINHAS = 5_000
MAX_COLUNAS = 60
AMOSTRA = 8

CAMPOS = (
    "name", "company", "value", "email", "phone",
    "whatsapp", "source", "owner", "notes", "status", "segment",
)
OBRIGATORIOS = ("name", "company")

LIMITES = {
    "name": 80, "company": 80, "email": 254, "phone": 32,
    "whatsapp": 32, "source": 60, "owner": 80, "notes": 4000,
}

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)

ETAPAS_IMPORTAVEIS = ("Prospecção", "Qualificação", "Proposta", "Negociação")

class ImportacaoInvalida(Exception):
    pass

def _neutralizar_formula(texto: str) -> str:
    if texto and texto[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + texto
    return texto

def _texto(valor: Any, limite: int, *, formula_segura: bool = False) -> str:
    t = str(valor or "").strip().replace("\x00", "")
    if formula_segura:
        t = _neutralizar_formula(t)
    return t[:limite]

def _parse_valor(bruto: Any) -> float:
    t = re.sub(r"[^\d,.\-]", "", str(bruto or "").strip())
    if not t or t in ("-", ".", ","):
        return 0.0
    lp, lv = t.rfind("."), t.rfind(",")
    if lp >= 0 and lv >= 0:
        dec, mil = (".", ",") if lp > lv else (",", ".")
        t = t.replace(mil, "").replace(dec, ".")
    elif lv >= 0:
        t = t.replace(",", ".") if (t.count(",") == 1 and len(t) - lv - 1 <= 2) else t.replace(",", "")
    elif lp >= 0 and not (t.count(".") == 1 and len(t) - lp - 1 <= 2):
        t = t.replace(".", "")
    valor = round(float(t), 2)
    if valor < 0 or valor > 1e9:
        raise ValueError("valor fora da faixa (0 a 1.000.000.000)")
    return valor

def _chave_tel(bruto: str) -> str:
    d = re.sub(r"\D", "", bruto or "")
    return d if len(d) >= 8 else ""

def parse(csv_texto: str, *, has_header: bool = True) -> tuple[list[str], list[list[str]]]:
    if not csv_texto or not csv_texto.strip():
        raise ImportacaoInvalida("O arquivo está vazio.")
    if len(csv_texto) > MAX_CSV_CHARS:
        raise ImportacaoInvalida("Arquivo grande demais. Divida em partes de até 2 MB.")

    primeira = next((l for l in csv_texto.splitlines() if l.strip()), "")
    sep = max([",", ";", "\t"], key=lambda c: primeira.count(c)) if primeira else ","

    leitor = csv.reader(io.StringIO(csv_texto), delimiter=sep)
    linhas = [linha for linha in leitor if any((c or "").strip() for c in linha)]
    if not linhas:
        raise ImportacaoInvalida("Não encontrei nenhuma linha de dados.")

    if has_header:
        cabecalhos = [(c or "").strip() for c in linhas[0]][:MAX_COLUNAS]
        dados = linhas[1:]
    else:
        largura = min(len(linhas[0]), MAX_COLUNAS)
        cabecalhos = [f"Coluna {i + 1}" for i in range(largura)]
        dados = linhas

    if len(dados) > MAX_LINHAS:
        raise ImportacaoInvalida(
            f"São {len(dados)} linhas; o limite por importação é {MAX_LINHAS}. Divida o arquivo."
        )
    return cabecalhos, dados

def sugerir_mapeamento(cabecalhos: list[str]) -> dict[str, str]:
    apelidos = {
        "name": ("nome", "name", "cliente", "contato", "lead", "responsavel pelo lead"),
        "company": ("empresa", "company", "companhia", "organizacao", "conta"),
        "value": ("valor", "value", "preco", "ticket", "montante", "receita"),
        "email": ("email", "e-mail", "mail"),
        "phone": ("telefone", "phone", "fone", "celular", "tel"),
        "whatsapp": ("whatsapp", "whats", "zap", "wpp"),
        "source": ("origem", "source", "fonte", "canal"),
        "owner": ("responsavel", "owner", "vendedor", "dono"),
        "notes": ("observacao", "observacoes", "notes", "nota", "anotacao", "obs"),
        "status": ("status", "etapa", "estagio", "fase"),
        "segment": ("segmento", "segment", "setor", "area"),
    }
    def _norm(s: str) -> str:
        return db.deburr(s).strip().lower() if hasattr(db, "deburr") else s.strip().lower()

    mapa: dict[str, str] = {}
    usados: set[str] = set()
    for campo, chaves in apelidos.items():
        for cab in cabecalhos:
            if cab in usados:
                continue
            n = _norm(cab)
            if any(n == _norm(k) or _norm(k) in n for k in chaves):
                mapa[campo] = cab
                usados.add(cab)
                break
    return mapa

def _linha_para_lead(
    cabecalhos: list[str], linha: list[str], mapping: dict[str, str]
) -> dict[str, Any]:
    indice = {cab: i for i, cab in enumerate(cabecalhos)}

    def bruto(campo: str) -> str:
        coluna = mapping.get(campo)
        if not coluna or coluna not in indice:
            return ""
        i = indice[coluna]
        return linha[i] if i < len(linha) else ""

    lead: dict[str, Any] = {}
    for campo in ("name", "company"):
        v = _texto(bruto(campo), LIMITES[campo], formula_segura=True)
        if not v:
            raise ValueError(f"{'Nome' if campo == 'name' else 'Empresa'} em branco")
        lead[campo] = v

    for campo in ("phone", "whatsapp", "source", "owner", "notes"):
        lead[campo] = _texto(bruto(campo), LIMITES[campo], formula_segura=(campo in ("source", "owner")))

    email = _texto(bruto("email"), LIMITES["email"]).lower()
    if email and not EMAIL_RE.match(email):
        raise ValueError(f"E-mail inválido: {email}")
    lead["email"] = email

    lead["value"] = _parse_valor(bruto("value")) if mapping.get("value") else 0.0

    etapa = _texto(bruto("status"), 20)
    lead["status"] = etapa if etapa in ETAPAS_IMPORTAVEIS else "Prospecção"

    seg = _texto(bruto("segment"), 20)
    lead["segment"] = seg if seg in db.SEGMENTS else "Outros"
    return lead

def _chaves_existentes(conn: db.Connection, user_id: int) -> dict[str, set]:
    linhas = conn.execute(
        "SELECT name, company, email, phone, whatsapp FROM leads WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    chaves = {"email": set(), "tel": set(), "nome_empresa": set()}
    for l in linhas:
        if (l["email"] or "").strip():
            chaves["email"].add(l["email"].strip().lower())
        for t in (l["phone"], l["whatsapp"]):
            k = _chave_tel(t or "")
            if k:
                chaves["tel"].add(k)
        chaves["nome_empresa"].add(f"{(l['name'] or '').lower()}|{(l['company'] or '').lower()}")
    return chaves

def _motivo_duplicado(lead: dict, chaves: dict[str, set]) -> str | None:
    if lead["email"] and lead["email"] in chaves["email"]:
        return "e-mail já cadastrado"
    for campo in ("phone", "whatsapp"):
        k = _chave_tel(lead.get(campo, ""))
        if k and k in chaves["tel"]:
            return "telefone já cadastrado"
    ne = f"{lead['name'].lower()}|{lead['company'].lower()}"
    if ne in chaves["nome_empresa"]:
        return "mesmo nome e empresa já existem"
    return None

def _registrar_chaves(lead: dict, chaves: dict[str, set]) -> None:
    if lead["email"]:
        chaves["email"].add(lead["email"])
    for campo in ("phone", "whatsapp"):
        k = _chave_tel(lead.get(campo, ""))
        if k:
            chaves["tel"].add(k)
    chaves["nome_empresa"].add(f"{lead['name'].lower()}|{lead['company'].lower()}")

def _mapa_efetivo(cabecalhos: list[str], mapping: dict[str, str]) -> dict[str, str]:
    usuario = {k: v for k, v in (mapping or {}).items() if v}
    return usuario if usuario else sugerir_mapeamento(cabecalhos)

def _percorrer(conn, user_id, cabecalhos, dados, mapping, has_header):
    chaves = _chaves_existentes(conn, user_id)
    for i, linha in enumerate(dados, start=2 if has_header else 1):
        try:
            lead = _linha_para_lead(cabecalhos, linha, mapping)
        except ValueError as erro:
            yield i, None, "erro", str(erro)
            continue
        dup = _motivo_duplicado(lead, chaves)
        if dup:
            yield i, lead, "duplicado", dup
            continue
        _registrar_chaves(lead, chaves)
        yield i, lead, "novo", ""

def analisar(conn: db.Connection, user_id: int, csv_texto: str,
             mapping: dict[str, str], has_header: bool = True) -> dict[str, Any]:
    cabecalhos, dados = parse(csv_texto, has_header=has_header)
    efetivo = _mapa_efetivo(cabecalhos, mapping)
    base: dict[str, Any] = {
        "colunas": cabecalhos,
        "mapeamento_sugerido": efetivo,
        "total": 0, "novos": 0, "duplicados": 0, "com_erro": 0, "amostra": [],
    }
    if not efetivo.get("name") or not efetivo.get("company"):
        return base

    novos = duplicados = com_erro = 0
    amostra: list[dict] = []
    for numero, lead, estado, motivo in _percorrer(conn, user_id, cabecalhos, dados, efetivo, has_header):
        if estado == "novo":
            novos += 1
        elif estado == "duplicado":
            duplicados += 1
        else:
            com_erro += 1
        if len(amostra) < AMOSTRA:
            amostra.append({
                "linha": numero, "estado": estado, "motivo": motivo,
                "nome": (lead or {}).get("name", ""), "empresa": (lead or {}).get("company", ""),
                "valor": (lead or {}).get("value", 0.0),
            })
    base.update(total=novos + duplicados + com_erro, novos=novos,
                duplicados=duplicados, com_erro=com_erro, amostra=amostra)
    return base

def importar(conn: db.Connection, user_id: int, csv_texto: str, mapping: dict[str, str],
             has_header: bool = True, pular_duplicados: bool = True) -> dict[str, Any]:
    cabecalhos, dados = parse(csv_texto, has_header=has_header)
    efetivo = _mapa_efetivo(cabecalhos, mapping)
    if not efetivo.get("name") or not efetivo.get("company"):
        raise ImportacaoInvalida("Escolha quais colunas são o Nome e a Empresa.")
    inseridos = pulados = com_erro = 0
    barrados_teto = 0
    erros: list[dict] = []
    agora = db.now_iso()

    espaco = crm.espaco_para_leads(conn, user_id)

    for numero, lead, estado, motivo in _percorrer(conn, user_id, cabecalhos, dados, efetivo, has_header):
        if estado == "erro":
            com_erro += 1
            if len(erros) < 50:
                erros.append({"linha": numero, "motivo": motivo})
            continue
        if estado == "duplicado" and pular_duplicados:
            pulados += 1
            continue

        if espaco < 1:

            barrados_teto += 1
            continue
        espaco -= 1

        cur = conn.execute(
            """INSERT INTO leads
                   (user_id, name, company, value, status, segment, email, phone,
                    whatsapp, source, owner, notes, tags,
                    stage_changed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)""",
            (
                user_id, lead["name"], lead["company"], lead["value"], lead["status"],
                lead["segment"], lead["email"], lead["phone"], lead["whatsapp"],
                lead["source"], lead["owner"], lead["notes"], agora, agora, agora,
            ),
        )
        lead_id = int(cur.lastrowid)
        activities.log(
            conn, user_id, lead_id=lead_id, kind="criacao",
            title="Lead importado", detail=f"Importado por CSV · etapa {lead['status']}",
            source="system",
        )
        inseridos += 1

    if inseridos:

        intel.marcar(conn, user_id, "primeiro_lead")
        intel.recalcular(conn, user_id)

    return {
        "inseridos": inseridos,
        "pulados_duplicados": pulados,
        "com_erro": com_erro,
        "barrados_limite": barrados_teto,
        "erros": erros,
    }
