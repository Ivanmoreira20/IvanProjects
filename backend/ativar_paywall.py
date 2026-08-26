from __future__ import annotations

import sys
from datetime import timedelta

import activities
import billing
import config
import db
import plans

DIAS_DE_CARENCIA = 30

def main(aplicar: bool) -> int:
    fim = billing.agora() + timedelta(days=DIAS_DE_CARENCIA)
    ate_texto = fim.strftime("%d/%m/%Y")
    titulo = "O plano gratuito vai ser encerrado"
    corpo = (
        f"A partir de {ate_texto} o Vertex passa a exigir um plano pago — o "
        f"Iniciante custa R$ 39,99/mês. Até lá nada muda para você, e a sua "
        f"conta continua funcionando normalmente. Seus dados são seus: a "
        f"exportação em CSV continua disponível, com ou sem assinatura."
    )

    tocadas, puladas = [], []
    with db.get_conn() as conn:
        contas = conn.execute("SELECT id, name, email FROM users ORDER BY id").fetchall()
        for u in contas:
            uid = int(u["id"])
            estado = billing.assinatura_conn(conn, uid)
            if estado.get("tem_acesso"):
                puladas.append((uid, u["email"], estado["status"]))
                continue
            tocadas.append((uid, u["email"]))
            if aplicar:
                billing.ativar(conn, uid, plans.INICIAL, fim, modo="carencia", centavos=0)
                activities.notify(
                    conn, uid,
                    type="cobranca", title=titulo, body=corpo, severity="warn",
                    dedup_key=f"paywall-carencia-{fim.date()}",
                )
        if not aplicar:

            conn.rollback()

    print(f"{'APLICADO' if aplicar else 'SIMULACAO (nada gravado)'}")
    print(f"carencia ate: {ate_texto}  ({DIAS_DE_CARENCIA} dias)")
    print(f"contas que ganham carencia: {len(tocadas)}")
    for uid, email in tocadas:
        print(f"   + conta {uid} <{email}>")
    print(f"contas puladas (ja tem acesso): {len(puladas)}")
    for uid, email, st in puladas:
        print(f"   . conta {uid} <{email}> [{st}]")
    if config.paywall_ativo():
        print("\nATENCAO: VERTEX_PAYWALL ja esta LIGADO neste servidor.")
    else:
        print("\nVERTEX_PAYWALL esta desligado -- ninguem foi bloqueado por este script.")
    return 0

if __name__ == "__main__":
    sys.exit(main("--aplicar" in sys.argv))
