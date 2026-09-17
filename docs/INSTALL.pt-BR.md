# Instalando o PhoneFlow

O PhoneFlow deixa você mandar uma tarefa de celular em linguagem natural pro seu
agente Plow — por iMessage ou pelo canvas — e ele executa num iPhone real:
abre apps, toca, digita, rola e te devolve o que encontrou. Funciona com
qualquer app; sem configuração por tarefa.

> **Idioma:** este guia está em português. English version:
> [INSTALL.md](INSTALL.md).

## Como as peças se encaixam

```
┌──────────────────────┐   LAN, :8788    ┌──────────────────────────┐
│  Host do agente      │◄───────────────►│  Seu Mac (Apple Silicon) │
│  (Docker)            │                 │  Plow Latch              │
│  PhoneFlow + Hermes  │                 │  iPhone Mirroring        │
└──────────────────────┘                 └──────────┬───────────────┘
                                                     │ iPhone bloqueado,
                                                     ▼ janela do mirror aberta
                                              ┌────────────┐
                                              │  iPhone    │
                                              └────────────┘
```

- O **host do agente** (uma máquina Linux ou um Mac; nos exemplos, `suedpc`)
  roda o PhoneFlow em Docker e guarda o cérebro (o planner).
- **Seu Mac** roda o Plow **Latch** e o **iPhone Mirroring**; todo toque, digitação
  e screenshot acontece nele. Pode ser a mesma máquina do host ou outra na mesma
  rede.
- O **iPhone** é controlado **bloqueado**, pelo iPhone Mirroring.

**Segurança:** só LAN — nunca exponha a porta 8788 na internet. A persona do
agente manda ele nunca agir em apps de banco, carteira ou senhas. Isso é uma
instrução, não um bloqueio de verdade: pra uma recusa garantida, liste os nomes
dos apps em `PHONEFLOW_BLOCKED_APPS`, que vem vazia por padrão.

## O que você precisa

- **Host do agente:** Docker + plugin docker compose, e a ferramenta
  [`plow-agents`](https://github.com/plow-pbc/plow-agents).
- **Mac:** macOS 15+ (Apple Silicon), Plow **Latch**, **iPhone Mirroring**
  pareado com seu iPhone, e as **Xcode Command Line Tools**
  (`xcode-select --install`) — os helpers do Mac são compilados com `swiftc`.
- **Uma chave de LLM** de um endpoint compatível com OpenAI (o planner). O
  endpoint padrão é `https://ollama.com/v1` e o modelo padrão é `glm-5.3-flash`
  (use `PHONEFLOW_AGENT_MODEL=kimi-k3` para raciocínio mais difícil).

---

## Parte 1 — O host do agente (Docker)

```sh
# no host do agente
git clone https://github.com/plow-pbc/plow-agents ~/plow-agents
export PATH="$HOME/plow-agents/bin:$PATH"

git clone https://github.com/visued/phoneflow ~/phoneflow
cd ~/phoneflow

plow-agents login          # autentica no Plow
plow-agents lines          # lista suas linhas
plow-agents mint ln_xxx    # grava ./plow-credentials ao lado do compose.yml

# o planner precisa de uma chave de LLM (tarefas de agente):
export PHONEFLOW_LLM_API_KEY=sk-...                    # sua chave OpenAI-compatível
export PHONEFLOW_LLM_BASE_URL=https://ollama.com/v1    # padrão
export PHONEFLOW_AGENT_MODEL=glm-5.3-flash             # planner padrão

docker compose up --build -d
```

> **Guarde o `plow-credentials` com cuidado — ele é a identidade do seu agente.**
> A página no Agent Index pertence ao agente Plow que a publicou primeiro, e o
> `plow-agents mint` sempre cria um agente **novo**. Nunca faça revoke e mint de
> novo: pra trocar a credencial use **`plow-agents rotate`**, e faça backup do
> arquivo (`cp -p plow-credentials ~/.config/plow/backups/`).

O primeiro build precisa de rede (baixa o cliente fixado do Agent Index).
Confirme que a API subiu:

```sh
curl -sf http://<host>:8788/api/health
# {"ok": true, "latch": "up"}   ← "latch" fica "down" até o lado do Mac ficar pronto
```

## Parte 2 — O Mac (Latch + iPhone Mirroring)

1. Instale e abra o **Plow Latch**; deixe rodando.
2. Abra o **iPhone Mirroring** e pareie com seu iPhone. Deixe a janela do mirror
   visível na **tela inicial**.
3. Dê as permissões do macOS **ao Latch** em Ajustes → Privacidade e Segurança:
   - **Gravação de Tela** — os prints da janela do mirror. Sem ela todo frame
     sai em branco.
   - **Acessibilidade** — cliques, arrastos, rolagens e teclas reais.
   - **Automação → System Events** — achar e focar a janela do mirror e apertar
     os atalhos de Início, Seletor de Apps e Spotlight. O macOS pergunta na
     primeira vez; responda **OK**.

   Depois de dar a Gravação de Tela, **feche e reabra o Latch** pra ele
   reconhecer. Os helpers rodam como filhos do Latch e herdam essas permissões.
4. Mantenha o iPhone **bloqueado** — o iPhone Mirroring só controla um telefone
   bloqueado (a sessão no Mac age como desbloqueada). Se você desbloquear, o
   mirror desconecta.

Com os dois rodando, o `/api/health` mostra `latch: "up"`.

## Os helpers do Mac

O iPhone Mirroring ignora quase todo input sintético, então o PhoneFlow traz
pequenos helpers que rodam no Mac. **Você nunca instala nenhum deles na mão.** No
primeiro uso o driver grava cada fonte em `~/.phoneflow/` via Latch, compila com
`xcrun swiftc -O` e guarda um hash do fonte ao lado do binário. Quando um helper
muda no repositório, ele é recompilado sozinho. A primeira execução custa cerca
de um minuto.

| Helper | O que faz |
|---|---|
| `pf_observe.sh` | **O helper que tira o print.** Em uma única chamada captura a tela, recorta a janela do mirror, amplia, roda OCR e detecção de ícones, e devolve um JSON com o texto reconhecido e o frame em JPEG base64. Substituiu cerca de 14 idas e voltas por passo. |
| `pf_ocr` | Reconhecimento de texto do macOS Vision, com a posição de cada linha. |
| `pf_icons` | Detector local de ícones em CoreML. Só é usado quando o modelo da Parte 3 está instalado. |
| `pf_drag` | Eventos reais de mouse: clique, toque longo e arrastar. |
| `pf_scroll` | Gesto de rolagem estilo trackpad com inércia, pra feeds passarem pro próximo item. |
| `pf_key` | Digita com key codes reais, que é o que o iPhone Mirroring repassa. |

## Parte 3 — Modelo de detecção de ícone (recomendado)

Pra tocar com precisão em ícones sem texto (lupa, sino, ícones da tab bar), o
PhoneFlow usa um modelo CoreML pequeno e local no Mac. Ele é **fornecido por
você** (não vem embutido). Instale uma vez no Mac:

```sh
# no Mac
pip3 install --user ultralytics huggingface_hub
python3 - <<'PY'
from huggingface_hub import hf_hub_download
from ultralytics import YOLO
import shutil, os
pt = hf_hub_download("microsoft/OmniParser-v2.0", "icon_detect/model.pt")
shutil.copy(pt, "icon_detect.pt")
YOLO("icon_detect.pt").export(format="coreml", imgsz=640, nms=True)
os.makedirs(os.path.expanduser("~/.phoneflow"), exist_ok=True)
shutil.rmtree(os.path.expanduser("~/.phoneflow/icon_detect.mlpackage"), ignore_errors=True)
shutil.copytree("icon_detect.mlpackage", os.path.expanduser("~/.phoneflow/icon_detect.mlpackage"))
print("instalado ~/.phoneflow/icon_detect.mlpackage")
PY
```

> **Nota de licença:** o `icon_detect` do OmniParser é **AGPL-3.0**. Aqui ele é
> usado como componente separado, fornecido por você (não redistribuído com o
> PhoneFlow, que é MIT). Sem ele, o PhoneFlow ainda funciona — cai pra OCR de
> texto e um grounder na nuvem pros ícones.

## Parte 4 — Registrar o agente (para iMessage)

Registre o PhoneFlow no Agent Index do Plow pra seu agente alcançá-lo:

```sh
docker compose exec agent /opt/hermes/.venv/bin/python3 \
  /opt/plow/agent-index-client.py --register --agent phoneflow-agent \
  --name "PhoneFlow" \
  --blurb "Manda uma tarefa de celular; roda no meu iPhone via Latch + iPhone Mirroring." \
  --runtime "Hermes"
```

(O container também se auto-registra no primeiro boot quando se vê sem registro.)

## Parte 5 — Usar

Com o Mac pronto (Latch up, iPhone bloqueado, mirror na tela inicial):

- **Pelo iMessage**, escreva pro seu agente Plow naturalmente:
  - `run wf_youtube_trending` — roda um workflow que você desenhou no canvas, ou
  - *"Abra o YouTube, vá na aba em alta (Hype) e me diga os títulos dos 3
    primeiros vídeos."* — tarefa em linguagem natural; sem workflow.
  - *"Abra o X, vá ao perfil oficial do @elonmusk e me diga o último post dele."*
- **Pelo canvas** em `http://<host>:8788`, desenhe um workflow e rode (opcional —
  o canvas não é necessário pro fluxo por iMessage).

O agente responde com o que encontrou (ex.: o texto do post, os títulos).

## Notas e dicas

- **O conhecimento de app é extensível.** Arquivos `app_hints/<app>.md` ensinam o
  layout do app, features renomeadas e popups. Já vêm: YouTube, Instagram,
  TikTok, WhatsApp, Ajustes, Safari, Spotify, Gmail, X. Adicione os seus
  soltando um novo `.md`.
- **O cursor é compartilhado.** Enquanto uma tarefa roda, ele move o cursor real
  do Mac pra clicar no mirror (o iPhone Mirroring só reage a eventos de mouse
  reais); ele devolve o cursor após cada clique, mas você não consegue usar o
  mouse ao mesmo tempo.
- **Apps bloqueados.** A lista vem vazia por padrão. Defina
  `PHONEFLOW_BLOCKED_APPS="C6,Nubank,Wallet"` pra o agente recusar apps por nome
  (busca por trecho, sem diferenciar maiúsculas).
- **Sem setup no telefone.** Sem Modo de Programador, sem WebDriverAgent, sem
  assinatura — só o iPhone Mirroring. É por isso também que apps de banco que
  bloqueiam automação podem não funcionar, e que nada precisa ser reassinado toda
  semana.
