"""Backup automatico do NRE Agenda, enviado por e-mail com link seguro (so administrador baixa).
Roda a cada hora no GitHub Actions: le a programacao em agenda_cfg/backup, e para cada item vencido
gera o arquivo, guarda em agenda_backup e envia o e-mail pelo EmailJS.
Variaveis: FIREBASE_SERVICE_ACCOUNT, EMAILJS_PRIVATE_KEY (so para enviar), BACKUP_SO_TESTAR=1 (gera e mede, sem gravar nem enviar)."""
import os, sys, json, gzip, base64, re, secrets, urllib.request, urllib.error, html
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
from robo import TZ, agora, nowstr, abrir_db

DIAS_LINK = 7
LIMITE_B64 = 940000
BASE = "https://agendanre.web.app/"
COLECOES = [("ag", "ag"), ("obs", "obs"), ("canc", "canc"), ("dia", "dia"), ("cfg", "cfg"), ("cad", "cad"), ("atd", "atd"), ("conf", "cf")]

def ms(dt): return int(dt.timestamp() * 1000)

def ocorrencia(it, agora_dt):
    """Ultima data/hora programada que ja passou (mesma regra do sistema)."""
    try: h, m = [int(x or 0) for x in str(it.get("hora") or "00:00").split(":")[:2]]
    except Exception: h, m = 0, 0
    d = agora_dt.replace(hour=h, minute=m, second=0, microsecond=0)
    js_dow = lambda x: x.isoweekday() % 7
    if it.get("freq") == "semanal":
        k = int(it.get("dow") or 0)
        d = d - timedelta(days=(js_dow(d) - k + 7) % 7)
        if d > agora_dt: d -= timedelta(days=7)
        return ms(d)
    if it.get("freq") == "mensal":
        dm = min(max(int(it.get("dom") or 1), 1), 28)
        d = d.replace(day=dm)
        if d > agora_dt:
            ant = (d.replace(day=1) - timedelta(days=1)).replace(day=dm)
            d = ant.replace(hour=h, minute=m)
        return ms(d)
    if d > agora_dt: d -= timedelta(days=1)
    return ms(d)

def gerar(db):
    dados = {}
    qt = {}
    for col, chave in COLECOES:
        dados[chave] = {d.id: d.to_dict() for d in db.collection("agenda_" + col).stream()}
    dados["geradoEm"] = nowstr()
    raw = json.dumps(dados, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    gz = gzip.compress(raw, 9)
    b64 = base64.b64encode(gz).decode()
    qt = {k: len(dados[k]) for k in ("ag", "canc", "cad", "atd")}
    return b64, len(raw), len(gz), qt

def enviar(conta, destino, assunto, msg):
    priv = os.environ.get("EMAILJS_PRIVATE_KEY", "").strip()
    if not priv: raise RuntimeError("EMAILJS_PRIVATE_KEY nao cadastrada no GitHub")
    corpo = html.escape(msg)
    corpo = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', corpo).replace("\n", "<br>")
    payload = {"service_id": conta["svc"], "template_id": conta["tpl"], "user_id": conta["key"], "accessToken": priv,
               "template_params": {"to_email": destino, "bcc_email": "", "subject": assunto, "message": msg, "message_html": corpo,
                                   "from_name": conta.get("nome") or conta["email"], "from_email": conta["email"], "reply_to": conta["email"]}}
    rq = urllib.request.Request("https://api.emailjs.com/api/v1.0/email/send", data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(rq, timeout=60) as r: return r.status
    except urllib.error.HTTPError as e:
        raise RuntimeError("EmailJS %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:160]))

def main():
    db = abrir_db()
    if not db: print("sem credencial do Firebase"); return
    testar = os.environ.get("BACKUP_SO_TESTAR") == "1"
    cfg = (db.collection("agenda_cfg").document("backup").get().to_dict() or {})
    lista = cfg.get("list") or []
    hist = cfg.get("hist") or []
    contas = ((db.collection("agenda_cfg").document("email").get().to_dict() or {}).get("list")) or []
    agora_dt = agora(); now = ms(agora_dt)
    print("programacoes:", len(lista), "| agora:", nowstr())
    if testar:
        b64, tam, gz, qt = gerar(db)
        print("TESTE: json %d KB, comprimido %d KB, base64 %d KB, limite %d KB, registros %s" % (tam // 1024, gz // 1024, len(b64) // 1024, LIMITE_B64 // 1024, qt))
        for it in lista: print(" item:", it.get("id"), it.get("freq"), it.get("hora"), "ativo" if it.get("on") is not False else "pausado", "ult=", it.get("ult"), "-> vencido" if it.get("ult") and ocorrencia(it, agora_dt) > it["ult"] else "-> em dia")
        return
    mudou = False
    for it in lista:
        if it.get("on") is False or not it.get("ult"): continue
        occ = ocorrencia(it, agora_dt)
        if occ <= it["ult"]: continue
        claim = "c_" + re.sub(r"[^A-Za-z0-9_-]", "", str(it.get("id"))) + "_" + str(occ)
        ref_claim = db.collection("agenda_backup").document(claim)
        try:
            ref_claim.create({"b64": "", "exp": occ + 8 * 86400000, "dt": nowstr(), "us": "Robô de backup", "tam": 0})
        except Exception as e:
            print("ja reservado por outro (sistema ou robo):", claim); continue
        try:
            conta = next((c for c in contas if c.get("id") == it.get("conta")), contas[0] if contas else None)
            if not conta: raise RuntimeError("conta de e-mail nao cadastrada")
            dest = str(it.get("email") or "").strip()
            if not re.match(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$", dest): raise RuntimeError("e-mail de destino invalido")
            b64, tam, gz, qt = gerar(db)
            if len(b64) > LIMITE_B64: raise RuntimeError("backup acima do limite para envio por link (%d KB comprimido)" % (gz // 1024))
            bid = secrets.token_hex(24); exp = now + DIAS_LINK * 86400000
            db.collection("agenda_backup").document(bid).create({"b64": b64, "exp": exp, "dt": nowstr(), "us": "Robô de backup", "tam": tam})
            url = BASE + "backup.html?id=" + bid
            ate = datetime.fromtimestamp(exp / 1000, TZ).strftime("%d/%m/%Y")
            msg = ("Backup do Sistema de Agendamento de Atendimentos, gerado automaticamente em %s.\n\nRegistros: %d agendamentos, %d cancelados, %d cadastros e %d atendimentos.\n\n"
                   "Para baixar o arquivo, entre no sistema com sua conta de administrador e abra o link até %s:\n%s\n\nO arquivo contém dados pessoais: não encaminhe este e-mail.") % (nowstr(), qt["ag"], qt["canc"], qt["cad"], qt["atd"], ate, url)
            try: enviar(conta, dest, "Backup do sistema de agendamento · " + nowstr(), msg)
            except Exception:
                db.collection("agenda_backup").document(bid).delete(); raise
            hist += [{"id": bid, "exp": exp}, {"id": claim, "exp": occ + 8 * 86400000}]
            it["ult"] = occ; it["ultEm"] = nowstr() + " (robô)"; it["err"] = ""
            print("backup enviado para", dest); mudou = True
        except Exception as e:
            try: ref_claim.delete()
            except Exception: pass
            it["err"] = nowstr() + " · robô · " + str(e)[:140]; mudou = True
            print("falha:", e)
    if mudou:
        atual = db.collection("agenda_cfg").document("backup").get().to_dict() or {}
        por_id = {x.get("id"): x for x in lista}
        nova = [por_id.get(x.get("id"), x) for x in (atual.get("list") or lista)]
        db.collection("agenda_cfg").document("backup").set({"list": nova, "hist": hist})

if __name__ == "__main__":
    main()
