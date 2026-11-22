# Robô diário do Agenda NRE

Roda de segunda a sexta, às 07:30 (Brasília), no GitHub Actions.

1. Lê no Firestore os atendimentos dos 4 serviços de aposentadoria que têm número de e-protocolo e ainda não foram publicados.
2. Consulta o e-Protocolo (consulta pública) e grava os andamentos novos. Ao aparecer "EMITIR RESOLUCAO" no setor de portarias e resoluções, muda a situação para "Resolução a emitir".
3. Busca no Diário Oficial (Poder Executivo) o número do protocolo nas edições desde essa data e confere o nome. Protocolo e nome conferindo: registra a publicação com o link das páginas (encontrada e seguinte). Só um dos dois: marca "A confirmar" para conferência humana.
4. Grava em `agenda_conf` uma linha por edição conferida e em `agenda_cfg/robo` o resultado da execução. Falha de consulta nunca vira "não consta".

## Configuração (uma vez)

1. Firebase: Configurações do projeto, Contas de serviço, Gerar nova chave privada (arquivo JSON).
2. GitHub: Settings, Secrets and variables, Actions, New repository secret, nome `FIREBASE_SERVICE_ACCOUNT`, valor = todo o conteúdo do JSON. Depois apague o arquivo baixado.
3. Firebase: publicar o `firestore.rules` deste repositório.
4. Actions, "Robo diario", Run workflow, para a primeira execução manual.

Sem o segredo, o robô roda em modo simulação (só consulta os sites, não lê nem grava o Firestore).
