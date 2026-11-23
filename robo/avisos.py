"""Avisos automaticos por e-mail do Agenda NRE (ligados em Configuracoes, aba Avisos automaticos; comecam desligados):
1) lembrete ao servidor na vespera do atendimento (proximo dia util), uma vez por agendamento;
2) relatorio mensal do mes anterior, uma vez por mes.
Roda de segunda a sexta, 08:00 (Brasilia), no GitHub Actions. Cada e-mail conta na cota do EmailJS (agenda_cfg/emailuso);
o robo reserva envios para o backup e para antes de a cota acabar.
Variaveis: FIREBASE_SERVICE_ACCOUNT, EMAILJS_PRIVATE_KEY, AVISOS_SO_TESTAR=1 (mostra o que enviaria, sem enviar nem gravar)."""
import os, sys, re
from datetime import timedelta
sys.path.insert(0, os.path.dirname(__file__))
from robo import agora, nowstr, abrir_db, log_robo
from backup import enviar, contar, ciclo_mail

LIM = 200
RESERVA = 10
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
OK_MAIL = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
AVI_ASS = "Lembrete do seu atendimento no NRE de Londrina em {data}"
AVI_TXT = ("Prezado(a) {nome}\n\nLembramos que o seu atendimento de {servico} está marcado para {quando}, {data}, às {hora}, "
           "no Núcleo Regional de Educação de Londrina\n\nCaso não consiga comparecer, responda a este e-mail para reagendarmos")

def br(d): return d.strftime("%d/%m/%Y")

def uso_atual(db):
    d = db.collection("agenda_cfg").document("emailuso").get()
    d = d.to_dict() if d.exists else {}
    try: dia = int(d.get("dia") or 21)
    except Exception: dia = 21
    if not 1 <= dia <= 28: dia = 21
    return int(d.get("n", 0)) if d.get("mes") == ciclo_mail(agora(), dia) else 0

def escolher_conta(contas, cid):
    ok = [c for c in contas if c.get("svc") and c.get("tpl") and c.get("key") and OK_MAIL.match(str(c.get("email") or ""))]
    return next((c for c in ok if c.get("id") == cid), ok[0] if ok else None)

def proximo_dia_util(hoje):
    d = hoje + timedelta(days=1)
    while d.weekday() >= 5: d += timedelta(days=1)
    return d

def primeiro_email(v):
    for x in re.split(r"[;,\s]+", str(v or "")):
        if OK_MAIL.match(x.strip()): return x.strip()
    return ""

def lembretes(db, conf, contas, testar):
    hoje = agora().date()
    if hoje.weekday() >= 5: print("lembretes: fim de semana, nada a fazer"); return
    conta = escolher_conta(contas, conf.get("conta"))
    if not conta: print("lembretes: conta de e-mail nao cadastrada"); return
    alvo = proximo_dia_util(hoje); iso = alvo.isoformat()
    quando = "amanhã" if (alvo - hoje).days == 1 else DIAS[alvo.weekday()]
    assunto_t = str(conf.get("assunto") or AVI_ASS); texto_t = str(conf.get("texto") or AVI_TXT)
    uso = uso_atual(db); env = pulo = sem = falha = 0; parou = False
    for d in db.collection("agenda_ag").where("date", "==", iso).stream():
        x = d.to_dict() or {}
        if (x.get("status") or "Agendado") != "Agendado" or not x.get("nome"): continue
        dest = primeiro_email(x.get("email"))
        if not dest: sem += 1; continue
        if LIM - RESERVA - uso - env <= 0: parou = True; break
        sub = {"{nome}": str(x["nome"]).split()[0].capitalize(), "{servico}": str(x.get("servico") or "atendimento"), "{data}": br(alvo),
               "{hora}": str(x.get("time") or ""), "{quando}": quando}
        assunto, msg = assunto_t, texto_t
        for k, v in sub.items(): assunto = assunto.replace(k, v); msg = msg.replace(k, v)
        if testar: print("TESTE lembrete ->", dest, "|", assunto); env += 1; continue
        claim = db.collection("agenda_avisos").document("l_" + re.sub(r"[^A-Za-z0-9_-]", "", d.id) + "_" + iso)
        try: claim.create({"em": nowstr(), "para": dest})
        except Exception: pulo += 1; continue
        try:
            enviar(conta, dest, assunto, msg); contar(db); env += 1
        except Exception as e:
            try: claim.delete()
            except Exception: pass
            falha += 1; print("falha ao enviar lembrete:", str(e)[:140])
    print("lembretes para %s: enviados %d, ja enviados antes %d, sem e-mail %d, falhas %d%s" % (br(alvo), env, pulo, sem, falha, ", parou por cota" if parou else ""))
    if not testar and (env or falha or parou):
        log_robo(db, "Envio", "E-mail", "Lembrete automático", "Atendimentos de %s: %d enviados, %d sem e-mail, %d falhas%s" % (br(alvo), env, sem, falha, " | parou para preservar a cota do EmailJS" if parou else ""))

def relatorio(db, conf, contas, testar):
    hoje = agora().date()
    try: dom = max(1, min(28, int(conf.get("dom") or 1)))
    except Exception: dom = 1
    if hoje.day < dom: print("relatorio: ainda nao e o dia", dom); return
    ult = hoje.replace(day=1) - timedelta(days=1); ini = ult.replace(day=1).isoformat(); fim = ult.isoformat()
    chave = "r_%04d-%02d" % (ult.year, ult.month)
    dests = [x for x in re.split(r"[;,\s]+", str(conf.get("dest") or "")) if OK_MAIL.match(x)]
    conta = escolher_conta(contas, conf.get("conta"))
    if not dests or not conta: print("relatorio: sem destinatario ou conta"); return
    ref = db.collection("agenda_avisos").document(chave)
    if not testar and ref.get().exists: print("relatorio de", chave, "ja enviado"); return
    if uso_atual(db) >= LIM - RESERVA: print("relatorio: cota do EmailJS no limite"); return
    por_st, por_sv, tot = {}, {}, 0
    for d in db.collection("agenda_ag").where("date", ">=", ini).where("date", "<=", fim).stream():
        x = d.to_dict() or {}; tot += 1
        st = x.get("status") or "Agendado"; por_st[st] = por_st.get(st, 0) + 1
        sv = x.get("servico") or "Sem serviço"; por_sv[sv] = por_sv.get(sv, 0) + 1
    canc = sum(1 for _ in db.collection("agenda_canc").where("date", ">=", ini).where("date", "<=", fim).stream())
    nome_mes = "%s de %d" % (MESES[ult.month - 1], ult.year)
    linhas = ["Relatório de atendimentos de %s" % nome_mes, "",
              "Agendamentos no mês: %d" % tot, "Cancelamentos no mês: %d" % canc, "", "Por status:"]
    linhas += ["%s: %d" % (k, v) for k, v in sorted(por_st.items(), key=lambda kv: -kv[1])]
    linhas += ["", "Por serviço:"] + ["%s: %d" % (k, v) for k, v in sorted(por_sv.items(), key=lambda kv: -kv[1])]
    linhas += ["", "Dados do Sistema de Agendamento de Atendimentos, gerados automaticamente em %s" % nowstr()]
    msg = "\n".join(linhas); assunto = "Relatório mensal de atendimentos · %02d/%d" % (ult.month, ult.year)
    if testar: print("TESTE relatorio ->", dests, "\n" + msg); return
    try: ref.create({"em": nowstr(), "para": ", ".join(dests)})
    except Exception: print("relatorio ja reservado por outra execucao"); return
    ok = 0
    for dest in dests:
        try: enviar(conta, dest, assunto, msg); contar(db); ok += 1
        except Exception as e: print("falha ao enviar relatorio a", dest, ":", str(e)[:140])
    if not ok:
        try: ref.delete()
        except Exception: pass
    log_robo(db, "Envio", "E-mail", "Relatório mensal", "%s | %d de %d destinatários" % (nome_mes, ok, len(dests)))

def main():
    db = abrir_db()
    if not db: print("sem credencial do Firebase"); return
    testar = os.environ.get("AVISOS_SO_TESTAR") == "1"
    avisos = db.collection("agenda_cfg").document("avisos").get().to_dict() or {}
    contas = ((db.collection("agenda_cfg").document("email").get().to_dict() or {}).get("list")) or []
    print("agora:", nowstr(), "| lembrete", "ligado" if (avisos.get("lemb") or {}).get("on") else "desligado", "| relatorio", "ligado" if (avisos.get("rel") or {}).get("on") else "desligado")
    if (avisos.get("lemb") or {}).get("on"): lembretes(db, avisos["lemb"], contas, testar)
    if (avisos.get("rel") or {}).get("on"): relatorio(db, avisos["rel"], contas, testar)

if __name__ == "__main__":
    main()
