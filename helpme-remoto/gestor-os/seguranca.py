"""Senha do painel com hash (PBKDF2) e bloqueio de tentativas."""
import hashlib, hmac, os, time

ITER = 240_000

def gerar_hash(senha):
    sal = os.urandom(16)
    return "pbkdf2$%d$%s$%s" % (ITER, sal.hex(), hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITER).hex())

def eh_hash(guardada):
    return str(guardada).startswith("pbkdf2$")

def confere(senha, guardada):
    """Aceita a senha guardada como hash ou, em instalacoes antigas, em texto puro."""
    if not senha or not guardada:
        return False
    if eh_hash(guardada):
        try:
            _, it, sal, h = guardada.split("$")
            return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), int(it)).hex(), h)
        except ValueError:
            return False
    return hmac.compare_digest(senha, guardada)

class Bloqueio:
    """Limite de tentativas erradas por chave (IP, numero da OS...) dentro de uma janela."""
    def __init__(self, maximo, janela_s):
        self.maximo, self.janela, self.reg = maximo, janela_s, {}
    def _limpa(self, chave):
        t = [x for x in self.reg.get(chave, []) if time.time() - x < self.janela]
        self.reg[chave] = t
        return t
    def bloqueado(self, chave):
        return len(self._limpa(chave)) >= self.maximo
    def falha(self, chave):
        self._limpa(chave).append(time.time())
        if len(self.reg) > 5000:                       # nao deixa a memoria crescer sem limite
            for k in [k for k in self.reg if not self._limpa(k)]:
                self.reg.pop(k, None)
    def limpar(self, chave):
        self.reg.pop(chave, None)
