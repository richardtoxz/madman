# Madman

Painel no Discord (e no navegador) para rodar ferramentas na sua conta:
limpar mensagens, gerenciar amigos e servidores, completar Quests, acompanhar
usuários e mais.

Cada conta roda num processo separado. O token da conta é digitado no painel e
fica só na memória, nunca em arquivo.

> **Aviso:** automatizar uma conta de usuário pode violar os Termos do Discord
> e levar ao bloqueio da conta. Use por sua conta e risco.

## O que você precisa

- Python 3.10 ou mais novo;
- um bot criado no [Discord Developer Portal](https://discord.com/developers/applications),
  com as intents `Members`, `Presences` e `Message Content` ligadas.

## Como instalar

Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python setup_envs.py
cp .env.example .env
```

Windows: troque `source .venv/bin/activate` por `.venv\Scripts\activate`.

Abra o `.env` e preencha `TOKEN_BOT_CONTROLE` com o token do bot. **Não**
coloque o token da sua conta nesse arquivo: ele é pedido no painel.

## Como usar

```bash
python scripts/check_environment.py   # confere se está tudo instalado
python -m panel.main                  # liga o bot e o painel web
```

- No Discord, use `/painel`.
- No navegador, abra `http://localhost:8080` e entre com o Discord. Para isso,
  preencha `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` e `DISCORD_REDIRECT_URI`
  no `.env`. O código da conta é pedido depois, já dentro do painel.
- Para ligar só o painel web: `python -m panel.main --web-only`.

Ações longas (limpeza, ações em massa, Quests) abrem uma DM com o progresso e
um botão para cancelar. A DM é apagada sozinha depois.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

## Funções

- **Mensagens:** apaga suas mensagens em DMs, grupos e servidores.
- **Amigos, pedidos e bloqueados:** remove ou aceita em massa.
- **Servidores:** sai de vários servidores de uma vez (com whitelist).
- **Quests:** completa Quests já aceitas no Discord, uma por vez. A recompensa
  continua sendo resgatada por você. Os endpoints não são oficiais e podem
  mudar sem aviso.
- **Tracker:** acompanha até 10 usuários por conta e manda para um webhook as
  mensagens, entradas em voz, mudanças de nome/avatar e presença. Não grava
  áudio nem guarda mensagens em disco. Configure o webhook em
  **Configurações → Webhook** e ligue em **Tracker → Ativar**.
- **Proteção:** reage a DMs suspeitas de contas desconhecidas.
- **Rich Presence:** edite ou desligue a atividade que o Madman mostra no seu
  perfil (aba Privacidade no site, ou Ferramentas → Rich Presence no Discord).

## Limpar mensagens pelo pacote de dados

Se você baixou seus dados do Discord, o script abaixo apaga as mensagens
listadas no pacote. Sem `--execute` ele só conta, sem tocar na conta:

```bash
python scripts/delete_data_package_messages.py "caminho/package.zip"
python scripts/delete_data_package_messages.py "caminho/package.zip" --execute
```

- Pede o token da conta na hora e não salva.
- Recusa rodar se a conta for diferente da que gerou o pacote.
- Vai um canal por vez, confere se tudo foi apagado e só fecha DMs individuais.
- Salva o progresso: se parar, é só rodar de novo.

## Para desenvolver

```bash
pip install -r requirements/dev.txt
python -m unittest discover -s tests   # testes
python -m ruff check <arquivos>        # lint
python -m ruff format <arquivos>       # formatação
python scripts/build_web_css.py        # gera o CSS do painel web
```

Para mexer no visual sem conta do Discord, use o modo demo:

```bash
python -m panel.main --demo            # painel com dados falsos em http://127.0.0.1:8090/__demo/entrar
```

Ele só escuta no próprio computador. A barra "DEMO", no canto da tela, troca entre
dados normais, listas vazias e erros, simula internet lenta e desconecta a conta.
Junto com `WEB_DEV_RELOAD=1`, a página recarrega sozinha a cada mudança no front.

Como o código está organizado:

- `panel/`: bot oficial e painel web, um pacote por função;
- `worker/`: processo que usa a conta, um pacote por função;
- `engine/`: abre os processos e conversa com eles;
- `tests/`: testes. Os testes de contrato e os de caracterização travam o
  comportamento atual. Se você mudar um comportamento de propósito, atualize o
  arquivo de referência com `python -m tests.<nome_do_teste> --update`.

Os pacotes `panel/lib/` e `worker/lib/` são gerados pelo `setup_envs.py`. Não
edite esses arquivos.
