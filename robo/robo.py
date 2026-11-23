"""Robo diario do Agenda NRE: acompanha no e-Protocolo e no Diario Oficial (Executivo) os atendimentos de aposentadoria.
Sem credencial (FIREBASE_SERVICE_ACCOUNT vazio) roda em modo simulacao: consulta os sites, nao le nem grava o Firestore."""
import os, sys, re, json, time, unicodedata, urllib.request, urllib.parse, urllib.error, http.cookiejar
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=-3))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
EPROT = "https://www.eprotocolo.pr.gov.br/spiweb/"
DOE = "https://www.dioe.pr.gov.br"
BUSCA = "https://dioe.pr.gov.br//busca/busca/buscar/query/0/di:%s/df:%s/?1=1&q=%s"
MON_SERV = ["APOSENTADORIA VOLUNTÁRIA", "APOSENTADORIA POR INVALIDEZ", "APOSENTADORIA COMPULSÓRIA", "APOSENTADORIA POR DEFICIÊNCIA"]
JANELA_DIAS = int(os.environ.get("JANELA_DIAS", "12"))
PAUSA = 2.0
NOME_ROBO = "Consulta automática"

def agora():
    return datetime.now(TZ)

def nowstr():
    return agora().strftime("%d/%m/%Y %H:%M")

def norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().upper()

def novo_opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def req(op, url, data=None, headers=None, tentativas=3):
    h = {"User-Agent": UA, "Accept": "text/html,application/json,*/*", "Accept-Language": "pt-BR,pt;q=0.9"}
    h.update(headers or {})
    ult = None
    for i in range(tentativas):
        try:
            with op.open(urllib.request.Request(url, data=data, headers=h), timeout=60) as f:
                return f.read()
        except urllib.error.HTTPError as e:
            ult = "HTTP %s" % e.code
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:
            ult = type(e).__name__
        time.sleep(3 * (i + 1))
    raise RuntimeError(ult or "falha")

# ---------------- e-Protocolo ----------------
class Tabela(HTMLParser):
    def __init__(self):
        super().__init__(); self.tabs = []; self.t = None; self.r = None; self.c = None
    def handle_starttag(self, tag, a):
        if tag == "table": self.t = []
        elif tag == "tr" and self.t is not None: self.r = []
        elif tag in ("td", "th") and self.r is not None: self.c = ""
    def handle_data(self, d):
        if self.c is not None: self.c += d
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.c is not None and self.r is not None:
            self.r.append(re.sub(r"\s+", " ", self.c).strip()); self.c = None
        elif tag == "tr" and self.r is not None and self.t is not None:
            if self.r: self.t.append(self.r)
            self.r = None
        elif tag == "table" and self.t is not None:
            self.tabs.append(self.t); self.t = None

def eprot_andamentos(num):
    op = novo_opener()
    req(op, EPROT + "consultarProtocoloDigital.do?action=iniciar")
    d = urllib.parse.urlencode([("acao", ""), ("consultaPublica", ""), ("consultaPorCpfCnpj", ""),
                                ("numeroProtocoloCon", ""), ("numeroProtocolo", num)]).encode()
    req(op, EPROT + "consultarProtocoloDigital.do?action=pesquisar", data=d,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    html = req(op, EPROT + "exibirProtocoloDigital.do?action=ajaxCarregarAndamentos&numeroProtocolo=" + urllib.parse.quote(num),
               headers={"X-Requested-With": "XMLHttpRequest"}).decode("utf-8", "ignore")
    p = Tabela(); p.feed(html)
    for t in p.tabs:
        if t and any("Local Para" in c for c in t[0]):
            linhas = []
            for r in t[1:]:
                if len(r) >= 5 and r[0].strip().isdigit():
                    linhas.append({"seq": int(r[0]), "data": r[1], "de": r[2], "para": r[3], "motivo": r[4]})
            return linhas
    raise RuntimeError("tabela de andamentos nao encontrada")

def eh_resolucao(l):
    p, m = norm(l["para"]), norm(l["motivo"])
    return "PORTARIAS E RESOLUCOES" in p and "EMITIR RESOLUCAO" in m

def data_iso(txt):
    m = re.search(r"(\d\d)/(\d\d)/(\d{4})", txt or "")
    return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1)) if m else ""

# ---------------- Diario Oficial ----------------
def lista_edicoes():
    op = novo_opener()
    j = json.loads(req(op, DOE + "/apifront/portal/edicoes/ultimas_edicoes.json?sem_limite=1&todos=1"))
    if j.get("erro"): raise RuntimeError("DOE devolveu erro")
    return j.get("itens", [])

def eh_executivo(it):
    n = norm(it.get("tipo_edicao_nome"))
    return "EXECUTIVO" in n and "SUPLEMENTO" not in n

def busca(termo, dia_iso):
    op = novo_opener()
    j = json.loads(req(op, BUSCA % (dia_iso, dia_iso, urllib.parse.quote('"%s"' % termo))))
    return j["hits"]["total"], j["hits"]["hits"]

def nome_confere(nome, conteudo):
    c = norm(conteudo)
    return bool(nome) and all(t in c for t in norm(nome).split() if len(t) > 2)

def link_pagina(ed_id, pag):
    return "%s/ver-flip/%s/#/e:%s/p:%s" % (DOE, ed_id, ed_id, pag)

def procura(item, ed):
    """Resultado da busca de um atendimento numa edicao: ('consta'|'confirmar'|'nao', pag, link) ou None se falhou."""
    dia = data_iso(ed["data"]); eid = str(ed["id"])
    try:
        tot, hits = busca(item["eprot"], dia); time.sleep(PAUSA)
        hits = [h for h in hits if str(h.get("_id", "")).startswith(eid + "_")]
        if hits:
            h = hits[0]; pag = int(str(h["_id"]).split("_")[1])
            cont = (h.get("_source") or {}).get("conteudo", "")
            ok = nome_confere(item.get("nome", ""), cont) if item.get("nome") else None
            return ("consta" if ok else "confirmar", pag, link_pagina(eid, pag), ok)
        if item.get("nome"):
            tot, hits = busca(item["nome"], dia); time.sleep(PAUSA)
            hits = [h for h in hits if str(h.get("_id", "")).startswith(eid + "_")
                    and nome_confere(item["nome"], (h.get("_source") or {}).get("conteudo", ""))]
            if hits:
                pag = int(str(hits[0]["_id"]).split("_")[1])
                return ("confirmar", pag, link_pagina(eid, pag), True)
        return ("nao", 0, "", None)
    except Exception as e:
        print("  falha na busca:", e); return None

def pg_txt(p):
    return "páginas %s e %s" % (p, p + 1)

# ---------------- Firestore ----------------
def log_robo(db, acao, onde, ref, det):
    """Registra no Logs do sistema (agenda_log) uma acao do robo."""
    try:
        import secrets as _s
        ts = int(datetime.now(TZ).timestamp() * 1000); lid = "%d_%s" % (ts, _s.token_hex(2))
        db.collection("agenda_log").document(lid).set({"id": lid, "ts": ts, "em": agora().strftime("%d/%m/%Y %H:%M:%S"),
            "por": NOME_ROBO, "acao": acao, "onde": onde, "ref": ref, "det": str(det)[:900]})
    except Exception as e:
        print("log nao registrado:", e)

def abrir_db():
    sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not sa: return None
    from google.oauth2 import service_account
    from google.cloud import firestore
    info = json.loads(sa)
    cred = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=info.get("project_id", "nre-agenda"), credentials=cred)

def main():
    inicio = agora(); hoje = inicio.date()
    db = abrir_db()
    so_pedido = os.environ.get("SO_SE_PEDIDO") == "1"
    if so_pedido:
        if not db: print("sem credencial"); sys.exit(0)
        pd = db.collection("agenda_cfg").document("robo_pedido").get().to_dict() or {}
        if pd.get("estado") != "pendente":
            print("nenhum pedido pendente"); sys.exit(0)
        print("pedido de consulta encontrado")
    status = {"ultimaExecucao": nowstr(), "ok": True, "erro": "", "eprotOk": True, "doeOk": True, "editoes": 0, "monitorados": 0, "modo": "real" if db else "simulacao"}
    # --- lista de monitorados
    if db:
        itens = []
        for d in db.collection("agenda_atd").stream():
            r = d.to_dict() or {}
            m = r.get("monit") or {}
            if r.get("servico") in MON_SERV and str(r.get("eprot", "")).strip() and m.get("estado", "and") != "pub":
                itens.append({"id": d.id, "eprot": r["eprot"].strip(), "nome": r.get("nome", ""), "monit": m})
    else:
        itens = []
        for x in filter(None, os.environ.get("TESTE_EPROT", "").split(";")):
            itens.append({"id": "T" + x, "eprot": x.strip(), "nome": "", "monit": {"estado": "res", "resolucaoEm": os.environ.get("TESTE_DESDE", "")}})
    status["monitorados"] = len(itens)
    print("monitorados:", len(itens), "| modo:", status["modo"])

    # --- etapa 1: e-Protocolo
    for it in itens:
        try:
            linhas = eprot_andamentos(it["eprot"]); time.sleep(PAUSA)
        except Exception as e:
            print(" e-Protocolo falhou:", e); status["eprotOk"] = False; continue
        m = it["monit"]; ult = max(linhas, key=lambda l: l["seq"]) if linhas else None
        vistos = int(m.get("eprotSeq", 0) or 0)
        novas = [l for l in sorted(linhas, key=lambda l: l["seq"]) if l["seq"] > vistos]
        res = next((l for l in sorted(linhas, key=lambda l: l["seq"]) if eh_resolucao(l)), None)
        it["linhas"] = len(linhas); it["resolucao"] = bool(res)
        print(" %s: %d andamentos, novos %d, emitir resolucao: %s" % (it["id"][:6], len(linhas), len(novas), "sim" if res else "nao"))
        if not db or not ult: continue
        ands = []
        if vistos == 0:
            ands.append("e-Protocolo: acompanhamento iniciado, último andamento em %s, de %s para %s, motivo %s" % (ult["data"][:10], ult["de"], ult["para"], ult["motivo"]))
        else:
            for l in novas:
                ands.append("e-Protocolo: %s, de %s para %s, motivo %s" % (l["data"][:10], l["de"], l["para"], l["motivo"]))
        novo_est = m.get("estado", "and")
        if res and novo_est == "and": novo_est = "res"
        if not ands and novo_est == m.get("estado", "and"): continue
        ref = db.collection("agenda_atd").document(it["id"])
        def aplicar(tx):
            snap = ref.get(transaction=tx); r = snap.to_dict() or {}
            mm = dict(r.get("monit") or {})
            if mm.get("estado") == "pub": return
            and_ = list(r.get("and") or [])
            for t in ands:
                and_.append({"dt": nowstr(), "un": "", "us": NOME_ROBO, "ds": t, "tp": "", "rb": 1})
            mm["eprotSeq"] = ult["seq"]; mm["eprotEm"] = nowstr()
            if novo_est != mm.get("estado", "and"):
                mm["estado"] = novo_est
                if novo_est == "res": mm["resolucaoEm"] = data_iso(res["data"])
            tx.update(ref, {"and": and_, "monit": mm})
        from google.cloud import firestore
        firestore.transactional(aplicar)(db.transaction())
        if novo_est == "res": it["monit"] = {**m, "estado": "res", "resolucaoEm": data_iso(res["data"])}

    # --- etapa 2: Diario Oficial (Executivo)
    try:
        eds = [e for e in lista_edicoes() if eh_executivo(e)]
        tipos = sorted({e.get("tipo_edicao_nome") for e in eds})
    except Exception as e:
        print("DOE falhou:", e); status["doeOk"] = False; eds = []; tipos = []
    print("cadernos Executivo reconhecidos:", tipos)
    lim = hoje - timedelta(days=JANELA_DIAS)
    cand = []
    for e in eds:
        di = data_iso(e.get("data"))
        if not di: continue
        dd = datetime.strptime(di, "%Y-%m-%d").date()
        if lim <= dd < hoje: cand.append((di, e))
    cand.sort(key=lambda x: x[0])
    print("edicoes na janela:", len(cand))
    confs = {}
    if db:
        for d in db.collection("agenda_conf").stream(): confs[d.id] = d.to_dict() or {}
    for di, e in cand:
        cid = "%s_%s" % (di, str(e.get("numero")).replace(".", ""))
        conf = confs.get(cid) or {"id": cid, "data": di, "ed": str(e.get("numero")), "cad": "Executivo", "orig": "robo", "itens": []}
        feitos = {x.get("id") for x in conf.get("itens", [])}
        mudou = cid not in confs
        for it in itens:
            m = it["monit"]
            if m.get("estado", "and") not in ("res", "conf"): continue
            desde = m.get("resolucaoEm") or ""
            if desde and di < desde: continue
            if it["id"] in feitos: continue
            r = procura(it, e)
            if r is None: status["doeOk"] = False; continue
            tipo, pag, lk, ok = r
            print(" ed %s item %s: %s%s" % (di, it["id"][:6], tipo, (" pag %s" % pag) if pag else ""))
            conf["itens"].append({"id": it["id"], "nome": it["nome"], "eprot": it["eprot"], "r": "c" if tipo in ("consta", "confirmar") else "n",
                                  "pg": ("%s e %s" % (pag, pag + 1)) if pag else "", "lk": lk, "auto": tipo})
            mudou = True
            if db and tipo in ("consta", "confirmar"):
                ref = db.collection("agenda_atd").document(it["id"])
                def aplicar2(tx, it=it, tipo=tipo, pag=pag, lk=lk, e=e, di=di):
                    snap = ref.get(transaction=tx); r = snap.to_dict() or {}; mm = dict(r.get("monit") or {})
                    if mm.get("estado") == "pub": return
                    and_ = list(r.get("and") or [])
                    if tipo == "consta":
                        and_.append({"dt": nowstr(), "un": "", "us": NOME_ROBO, "ds": "Publicado no Diário Oficial do Estado nº %s, de %s, %s" % (e.get("numero"), "%s/%s/%s" % (di[8:], di[5:7], di[:4]), pg_txt(pag)),
                                     "tp": "", "pb": 1, "lk": [{"t": "Acesso ao Diário", "u": lk}]})
                        mm.update({"estado": "pub", "pubEd": str(e.get("numero")), "pubDt": di, "pubPg": "%s e %s" % (pag, pag + 1), "pubLk": lk, "pubEm": nowstr(), "pubPor": NOME_ROBO})
                    else:
                        and_.append({"dt": nowstr(), "un": "", "us": NOME_ROBO, "rb": 1,
                                     "ds": "Possível publicação no Diário Oficial nº %s, de %s, %s, a confirmar (nome ou protocolo não conferem por completo). Confira e use Registrar publicação" % (e.get("numero"), "%s/%s/%s" % (di[8:], di[5:7], di[:4]), pg_txt(pag)),
                                     "tp": "", "lk": [{"t": "Acesso ao Diário", "u": lk}]})
                        mm.update({"estado": "conf", "pend": {"ed": str(e.get("numero")), "dt": di, "pg": "%s e %s" % (pag, pag + 1), "lk": lk}})
                    tx.update(ref, {"and": and_, "monit": mm})
                from google.cloud import firestore
                firestore.transactional(aplicar2)(db.transaction())
                m["estado"] = "pub" if tipo == "consta" else "conf"
                log_robo(db, "Alteração", "Atendimento", it["id"][:8] + " · " + str(it.get("nome", "")), ("Publicação localizada no Diário Oficial nº " if tipo == "consta" else "Possível publicação a confirmar, edição nº ") + str(e.get("numero")) + ", página " + str(pag))
        if mudou and status["doeOk"]:
            conf["em"] = nowstr(); conf["us"] = NOME_ROBO
            if db: db.collection("agenda_conf").document(cid).set(conf)
            status["editoes"] += 1
    status["ok"] = status["eprotOk"] and status["doeOk"]
    if not status["ok"]: status["erro"] = "Falha ao consultar " + ("e-Protocolo " if not status["eprotOk"] else "") + ("Diário Oficial" if not status["doeOk"] else "")
    print("status:", json.dumps({k: v for k, v in status.items()}, ensure_ascii=False))
    if db:
        log_robo(db, "Consulta", "Diário Oficial", "e-Protocolo e Diário Oficial", ("Concluída" if status["ok"] else "Com falha: " + str(status.get("erro") or "")) + " | edições conferidas: " + str(status.get("editoes", 0)))
        db.collection("agenda_cfg").document("robo").set(status)
        ped = db.collection("agenda_cfg").document("robo_pedido")
        if (ped.get().to_dict() or {}).get("estado") == "pendente":
            ped.update({"estado": "atendido" if status["ok"] else "falhou", "atendidoEm": nowstr(), "erro": status["erro"]})
    sys.exit(0 if status["ok"] else 1)

if __name__ == "__main__":
    main()
