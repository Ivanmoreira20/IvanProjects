from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORTA_PREFERIDA = 8000
PORTAS_PARA_TENTAR = 25
SEGUNDOS_ATE_DESISTIR = 20.0

CONGELADO = bool(getattr(sys, "frozen", False))

LINHA = "=" * 66

def _pasta_do_executavel() -> Path:
    if CONGELADO:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _pasta_dos_dados() -> Path:
    if CONGELADO:
        return Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
    return Path(__file__).resolve().parent

HOME = _pasta_do_executavel()
DADOS = _pasta_dos_dados()
BACKEND = DADOS / "backend"
ARQUIVO_DE_ERRO = HOME / "erro.log"

def _console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (ValueError, OSError):
                pass

def diga(mensagem: str = "") -> None:
    try:
        print(mensagem, flush=True)
    except (ValueError, OSError):
        pass

def _tem_console() -> bool:
    try:
        return bool(sys.stdout) and sys.stdout.isatty()
    except (ValueError, AttributeError):
        return False

def esperar_tecla() -> None:
    if not _tem_console():
        return
    try:
        if os.name == "nt":
            import msvcrt

            diga("\nPressione qualquer tecla para fechar...")
            msvcrt.getch()
        else:
            input("\nPressione ENTER para fechar...")
    except (KeyboardInterrupt, EOFError, OSError, ImportError):
        pass

def registrar_erro(titulo: str, erro: BaseException | None = None) -> None:
    detalhe = "".join(traceback.format_exception(erro)) if erro is not None else ""
    diga()
    diga(LINHA)
    diga("  NAO FOI POSSIVEL INICIAR O VERTEX CRM")
    diga(LINHA)
    diga(f"  {titulo}")
    if erro is not None:
        diga(f"  Motivo: {type(erro).__name__}: {erro}")
    try:
        with ARQUIVO_DE_ERRO.open("w", encoding="utf-8") as arquivo:
            arquivo.write(f"Vertex CRM -- {time.strftime('%d/%m/%Y %H:%M:%S')}\n")
            arquivo.write(f"{titulo}\n\n")
            arquivo.write(f"executavel : {sys.executable}\n")
            arquivo.write(f"pasta      : {HOME}\n")
            arquivo.write(f"dados      : {DADOS}\n")
            arquivo.write(f"empacotado : {CONGELADO}\n")
            arquivo.write(f"python     : {sys.version}\n\n")
            arquivo.write(detalhe or "(sem traceback)\n")
        diga(f"  Detalhes salvos em: {ARQUIVO_DE_ERRO}")
    except OSError as falha_ao_gravar:
        diga(f"  (nao consegui gravar o erro.log: {falha_ao_gravar})")
        diga()
        diga(detalhe)
    diga(LINHA)

def preparar_ambiente() -> None:
    os.environ["VERTEX_HOME"] = str(HOME)

    if CONGELADO:

        os.environ.setdefault("VERTEX_DB", str(HOME / "vertex.db"))

        sys.dont_write_bytecode = True
        try:
            os.chdir(HOME)
        except OSError:
            pass

    caminho = str(BACKEND)
    if caminho not in sys.path:
        sys.path.insert(0, caminho)

def pasta_aceita_gravacao(pasta: Path) -> bool:
    teste = pasta / ".vertex_teste_de_escrita"
    try:
        teste.write_bytes(b"ok")
    except OSError:
        return False
    try:
        teste.unlink()
    except OSError:
        pass
    return True

def porta_livre(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tomada:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                tomada.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        try:
            tomada.bind((HOST, porta))
        except OSError:
            return False
    return True

def escolher_porta() -> int:
    for porta in range(PORTA_PREFERIDA, PORTA_PREFERIDA + PORTAS_PARA_TENTAR):
        if porta_livre(porta):
            return porta
    raise RuntimeError(
        f"Nenhuma porta livre entre {PORTA_PREFERIDA} e "
        f"{PORTA_PREFERIDA + PORTAS_PARA_TENTAR - 1}."
    )

class Servidor:

    def __init__(self, aplicacao: object, porta: int) -> None:
        import uvicorn

        self.porta = porta
        self.falha: BaseException | None = None
        self.parou = threading.Event()
        self._servidor = uvicorn.Server(
            uvicorn.Config(
                aplicacao,
                host=HOST,
                port=porta,
                log_level="info",
                access_log=False,

            )
        )
        self._thread = threading.Thread(target=self._rodar, name="vertex-server", daemon=True)

    def _rodar(self) -> None:
        try:
            self._servidor.run()
        except BaseException as erro:  # noqa: BLE001 -- precisa chegar ate o console
            self.falha = erro
        finally:
            self.parou.set()

    def iniciar(self) -> None:
        self._thread.start()

    def morreu(self) -> bool:
        return self.parou.is_set()

    def encerrar(self, segundos: float = 8.0) -> None:
        self._servidor.should_exit = True
        self._thread.join(timeout=segundos)

    def aguardar(self) -> None:
        while self._thread.is_alive():
            self._thread.join(timeout=0.5)

def _liberar_ctrl_c() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    except Exception:  # noqa: BLE001 -- puro conforto, nunca fatal
        pass

def instalar_tratador_de_fechamento(servidor: "Servidor") -> object | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        CTRL_CLOSE_EVENT = 2
        CTRL_LOGOFF_EVENT = 5
        CTRL_SHUTDOWN_EVENT = 6
        PROTOTIPO = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def tratar(evento: int) -> bool:
            if evento in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
                servidor.encerrar(segundos=4.0)
                return True

            return False

        callback = PROTOTIPO(tratar)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(callback, True)
        return callback
    except Exception:  # noqa: BLE001 -- sem isso o app ainda funciona
        return None

def esperar_responder(porta: int, servidor: Servidor, limite: float) -> bool:
    import urllib.error
    import urllib.request

    alvo = f"http://{HOST}:{porta}/"
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if servidor.morreu():
            return False
        try:
            with urllib.request.urlopen(alvo, timeout=2.0) as resposta:  # noqa: S310
                resposta.read(1)
            return True
        except urllib.error.HTTPError:

            return True
        except Exception:  # noqa: BLE001 -- ainda subindo
            time.sleep(0.25)
    return False

def _avisar_porta_alternativa(porta: int) -> None:
    diga()
    diga(f"  ATENCAO: a porta {PORTA_PREFERIDA} ja estava ocupada -- usando a {porta}.")
    diga("  O login com Google (OAuth) esta registrado no Google para o endereco")
    diga(f"  http://{HOST}:{PORTA_PREFERIDA} e provavelmente NAO vai funcionar nesta")
    diga("  porta. Se precisar dele, feche o outro programa que esta usando a")
    diga(f"  porta {PORTA_PREFERIDA} (ou a outra janela do Vertex CRM) e abra de novo.")

def main() -> int:
    _console_utf8()
    _liberar_ctrl_c()
    preparar_ambiente()

    diga()
    diga(LINHA)
    diga("  VERTEX CRM")
    diga(LINHA)
    diga("  Vertex CRM iniciando...")

    if CONGELADO and not pasta_aceita_gravacao(HOME):
        diga()
        diga(LINHA)
        diga("  NAO FOI POSSIVEL INICIAR O VERTEX CRM")
        diga(LINHA)
        diga("  O Windows nao deixa gravar arquivos nesta pasta:")
        diga(f"    {HOME}")
        diga()
        diga("  O Vertex CRM guarda os seus dados (vertex.db) ao lado do proprio")
        diga("  programa, entao ele precisa poder escrever aqui.")
        diga()
        diga("  Mova o VertexCRM.exe para uma pasta sua -- Documentos ou a Area de")
        diga("  Trabalho, por exemplo -- e abra de novo. Evite 'Arquivos de")
        diga("  Programas' e pastas de rede somente leitura.")
        diga(LINHA)
        esperar_tecla()
        return 1

    try:
        aplicacao_web = _carregar_aplicacao()
    except BaseException as erro:  # noqa: BLE001
        registrar_erro("Falhei ao carregar o servidor (arquivos do programa).", erro)
        esperar_tecla()
        return 1

    try:
        porta = escolher_porta()
    except BaseException as erro:  # noqa: BLE001
        registrar_erro("Nao encontrei nenhuma porta livre nesta maquina.", erro)
        esperar_tecla()
        return 1

    endereco = f"http://{HOST}:{porta}"

    endereco_app = f"{endereco}/app"

    try:
        servidor = Servidor(aplicacao_web, porta)
        servidor.iniciar()
    except BaseException as erro:  # noqa: BLE001
        registrar_erro("Falhei ao subir o servidor.", erro)
        esperar_tecla()
        return 1

    _tratador_de_fechamento = instalar_tratador_de_fechamento(servidor)  # noqa: F841

    if not esperar_responder(porta, servidor, SEGUNDOS_ATE_DESISTIR):
        if servidor.morreu():
            motivo = (
                "O servidor parou durante a partida. A causa costuma estar nas "
                "linhas de ERROR logo acima, antes desta moldura."
            )
        else:
            motivo = (
                f"O servidor nao respondeu em {endereco} depois de "
                f"{int(SEGUNDOS_ATE_DESISTIR)} segundos."
            )
        registrar_erro(motivo, servidor.falha)
        servidor.encerrar(segundos=3.0)
        esperar_tecla()
        return 1

    if porta != PORTA_PREFERIDA:
        _avisar_porta_alternativa(porta)

    diga()
    diga(f"  Pronto. O CRM esta em:  {endereco_app}")
    diga(f"  A pagina de apresentacao esta em: {endereco}")
    diga(f"  Seus dados ficam em: {os.environ.get('VERTEX_DB', '(padrao do backend)')}")
    diga()
    diga("  Abrindo o navegador padrao...")

    try:
        webbrowser.open(endereco_app)
    except Exception:  # noqa: BLE001 -- sem navegador nao e motivo para cair
        diga(f"  Nao consegui abrir o navegador. Acesse manualmente: {endereco_app}")

    diga()
    diga("  Feche esta janela para encerrar o servidor.")
    diga("  (ou pressione Ctrl+C aqui dentro)")
    diga(LINHA)
    diga()

    try:
        servidor.aguardar()
    except KeyboardInterrupt:
        diga()
        diga("  Encerrando o Vertex CRM...")
        servidor.encerrar()

    if servidor.falha is not None and not isinstance(servidor.falha, KeyboardInterrupt):
        registrar_erro("O servidor parou sozinho.", servidor.falha)
        esperar_tecla()
        return 1

    diga("  Vertex CRM encerrado. Ate logo.")
    return 0

def _carregar_aplicacao() -> object:
    if not (BACKEND / "app.py").exists():
        raise FileNotFoundError(f"nao achei app.py em {BACKEND}")
    import app as modulo_backend

    return modulo_backend.app

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as _erro_inesperado:  # noqa: BLE001
        registrar_erro("Erro inesperado.", _erro_inesperado)
        esperar_tecla()
        raise SystemExit(1) from _erro_inesperado
