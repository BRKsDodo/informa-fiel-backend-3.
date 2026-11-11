# Informa Fiel — coletor inicial

Este pacote inicia a **coleta de pautas e deduplicação**, ainda sem publicar matérias completas.

## Fontes incluídas
- Meu Timão: somente URLs editoriais `/noticias-do-corinthians/<id>/`. O código não consulta `/forum`, `/torcida` ou comentários.
- ge: feed RSS geral, filtrado por referências ao Corinthians.

## O que faz
- Busca manchetes e metadados editoriais.
- Filtra pautas relacionadas ao Corinthians.
- Agrupa manchetes semelhantes de diferentes veículos.
- Salva os candidatos em `docs/news.json`.
- Executa manualmente ou a cada 30 minutos via GitHub Actions.

## O que NÃO faz ainda
- Não copia o texto integral dos portais.
- Não gera matérias com IA.
- Não publica automaticamente no AppCreator24.
- Não contorna bloqueios, paywalls ou restrições de acesso.
- Não garante que todo agrupamento semântico esteja correto: a deduplicação é uma primeira triagem e precisa de revisão.

## Configuração
1. Crie um repositório GitHub privado e envie os arquivos mantendo as pastas.
2. Em Settings > Actions > General, permita que Actions faça commits no repositório (se necessário).
3. Em Actions, execute `Update Informa Fiel news candidates` com `Run workflow`.
4. Depois de uma execução bem-sucedida, confira `docs/news.json`.
5. Para publicar o JSON em GitHub Pages, habilite Pages usando a branch `main` e a pasta `/docs` (se essa opção estiver disponível na configuração do repositório).

## Nota sobre Meu Timão
A página de notícias e o fórum são áreas distintas. Este coletor aceita apenas links cujo caminho corresponda a `/noticias-do-corinthians/<número>/`, descartando links de fórum, torcida e comentários. A coleta deve respeitar os termos e limites técnicos do site; se houver feed oficial ou autorização específica, prefira esse método.
