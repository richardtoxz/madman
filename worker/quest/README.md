# Quest worker

Implementação baseada principalmente no modelo de execução do
[Equicord Questify](https://github.com/Equicord/Equicord/tree/main/src/equicordplugins/questify),
adaptada para o worker Python do Madman.

## Comportamento

- Processa uma Quest por vez.
- Exige que a Quest já tenha sido aceita manualmente no Discord.
- Mantém o resgate da recompensa manual.
- Aguarda tempo real nas Quests de vídeo.
- Usa heartbeats limitados e interrompe quando o servidor não avança.
- Só informa conclusão após confirmação do estado retornado pelo Discord.
- Cancela e encerra sessões HTTP/tarefas auxiliares no shutdown.

## Tipos tratados automaticamente

- `WATCH_VIDEO` e `WATCH_VIDEO_ON_MOBILE`;
- `PLAY_ON_DESKTOP`, `PLAY_ON_XBOX` e `PLAY_ON_PLAYSTATION`;
- `PLAY_ACTIVITY`, quando existe um canal de voz/DM válido;
- `ACHIEVEMENT_IN_ACTIVITY`, com proxy ticket e autorização temporária.

`PLAY_ON_DESKTOP_V2`, `ACHIEVEMENT_IN_GAME` e `STREAM_ON_DESKTOP` são
reconhecidos, mas ficam manuais porque o Questify atual não os inclui na lista
de conclusão automática.

O Questify executa dentro do Discord Desktop e usa ações internas encontradas
no bundle. O Madman é um worker externo; portanto, replica o protocolo e as
validações observáveis, mas não consegue reproduzir os patches internos de
stack trace do cliente Electron.

Os endpoints de Quest não fazem parte da API pública do Discord e podem mudar
sem aviso. Por isso, falhas de formato e tipos desconhecidos são tratadas como
incompatibilidade, nunca como sucesso.
