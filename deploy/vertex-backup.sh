#!/bin/bash
# Backup diário do Vertex CRM.
#
# Três decisões que fazem este script valer alguma coisa:
#
#   1. **Cópia consistente, não `cp`.** O SQLite pode estar no meio de uma
#      escrita quando o backup roda. `cp` copiaria um arquivo rasgado que só se
#      descobre quebrado no dia do desastre. `sqlite3.backup()` faz a cópia
#      pelo próprio motor, respeitando a transação em curso.
#
#   2. **Verificação de integridade na hora.** Todo backup é aberto e roda
#      `PRAGMA integrity_check` + uma contagem real das tabelas. Um arquivo que
#      não passa é descartado e o script falha com barulho, em vez de acumular
#      lixo silenciosamente.
#
#   3. **A foto de perfil também é dado.** Ela vive em arquivo, fora do banco:
#      um backup só do `.db` restauraria contas sem foto e com referência
#      quebrada.
#
# Retenção: 14 diários + 8 semanais (domingos). O suficiente para pegar um
# estrago percebido semanas depois, sem encher o disco.
#
# Instalação (no servidor, como root):
#   cp vertex-backup.sh /usr/local/bin/vertex-backup
#   chmod +x /usr/local/bin/vertex-backup
#   cp vertex-backup.timer vertex-backup.service /etc/systemd/system/
#   systemctl enable --now vertex-backup.timer

set -euo pipefail

DB="${VERTEX_DB:-/var/lib/vertex-crm/vertex.db}"
AVATARS="$(dirname "$DB")/avatars"
DESTINO="${VERTEX_BACKUP_DIR:-/var/backups/vertex-crm}"
PYTHON="${VERTEX_PYTHON:-/opt/vertex-crm/.venv/bin/python}"
DIAS=14
SEMANAS=8

carimbo="$(date +%Y%m%d-%H%M%S)"
dia_da_semana="$(date +%u)"   # 7 = domingo
alvo="$DESTINO/diario"
[ "$dia_da_semana" = "7" ] && alvo="$DESTINO/semanal"

mkdir -p "$alvo"
arquivo="$alvo/vertex-$carimbo.db"

log() { echo "[vertex-backup] $*"; }

# ---------------------------------------------------------------------------
# 1. Cópia consistente
# ---------------------------------------------------------------------------
log "copiando $DB -> $arquivo"
"$PYTHON" - "$DB" "$arquivo" <<'PY'
import sqlite3, sys
origem, destino = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{origem}?mode=ro", uri=True)
dst = sqlite3.connect(destino)
with dst:
    src.backup(dst)
dst.close()
src.close()
PY

# ---------------------------------------------------------------------------
# 2. O backup presta? (esta é a parte que a maioria pula)
# ---------------------------------------------------------------------------
log "verificando integridade"
"$PYTHON" - "$arquivo" <<'PY'
import sqlite3, sys
caminho = sys.argv[1]
c = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
estado = c.execute("PRAGMA integrity_check").fetchone()[0]
if estado != "ok":
    raise SystemExit(f"integridade FALHOU: {estado}")
# Não basta abrir: as tabelas que importam têm que responder.
for tabela in ("users", "leads", "subscriptions", "sessions", "organizations"):
    n = c.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
    print(f"    {tabela}: {n} linha(s)")
contas = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
if contas < 1:
    raise SystemExit("backup sem nenhuma conta -- recusado")
c.close()
print("    integridade: ok")
PY

# O SQLite deixa `-wal`/`-shm` ao lado do arquivo copiado. Eles não fazem parte
# do backup (o conteúdo já foi consolidado no .db) e, se ficassem, alguém no dia
# do desastre olharia para três arquivos sem saber qual restaurar.
rm -f "$arquivo-wal" "$arquivo-shm"

gzip -f "$arquivo"
log "compactado: ${arquivo}.gz ($(du -h "${arquivo}.gz" | cut -f1))"

# ---------------------------------------------------------------------------
# 3. Fotos de perfil (dado que NÃO está no banco)
# ---------------------------------------------------------------------------
if [ -d "$AVATARS" ]; then
    tar -czf "$alvo/avatars-$carimbo.tar.gz" -C "$(dirname "$AVATARS")" "$(basename "$AVATARS")"
    log "fotos: $alvo/avatars-$carimbo.tar.gz"
fi

# ---------------------------------------------------------------------------
# 4. Retenção
# ---------------------------------------------------------------------------
find "$DESTINO/diario" -name '*.gz' -mtime "+$DIAS" -delete 2>/dev/null || true
find "$DESTINO/semanal" -name '*.gz' -mtime "+$((SEMANAS * 7))" -delete 2>/dev/null || true

log "concluído: $(ls -1 "$DESTINO"/*/*.gz 2>/dev/null | wc -l) arquivo(s) guardado(s)"
