# Madman

Painel de controle em Discord para executar ferramentas de conta em workers
Python isolados por usuário. O token da conta é enviado pelo modal e permanece
somente em memória enquanto a sessão estiver ativa.

> O uso de selfbots e automações de conta pode violar os Termos do Discord e
> resultar em limitação ou encerramento da conta. Use por sua responsabilidade.

## Requisitos

- Arch Linux ou outra distribuição Linux;
- Python 3.10 ou superior (validado atualmente com Python 3.13);
- `python-pip` e `python-virtualenv`;
- intents de `Members`, `Presences` e `Message Content` habilitadas para o bot
  oficial no Developer Portal.

## Instalação local

```bash
python -m venv .venv
source .venv/bin/activate
python setup_envs.py
cp .env.example .env
chmod 600 .env
```

Preencha pelo menos `TOKEN_BOT_CONTROLE` no `.env`. Os tokens das contas de
usuário não devem ser colocados nesse arquivo; eles são informados no modal.

Valide e execute:

```bash
python scripts/check_environment.py
python -m unittest discover -s tests -v
python -m panel.main
```

## Docker

```bash
cp .env.example .env
chmod 600 .env
docker compose up --build
```

O container executa como usuário sem privilégios, monta o `.env` como somente
leitura e publica o health check HTTP na porta `8080`.

## Arquitetura

- `panel/`: bot oficial, modais e interface;
- `engine/`: sessões, subprocessos e IPC;
- `worker/`: cliente de conta e implementações das funções;
- `ipc_protocol.py`: protocolo correlacionado entre painel e workers;
- `tests/`: regressões de isolamento, segurança e comportamento.

Detalhes e limitações do Quest Completer estão em
[`worker/quest/README.md`](worker/quest/README.md).
