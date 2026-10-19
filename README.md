# NuMelhor Delivery

Site de pedidos da NuMelhor Lanches (Av. Luiz Lazzarin, 155, Rio Maina, Criciúma/SC). Feito para ser simples até para quem tem pouca prática com celular: letras grandes, botões grandes e escritos, e poucos passos. Funciona de 320px (celular pequeno) até telas grandes.

- **Cardápio real da loja:** xis, hambúrgueres, pastéis (salgados, especiais e doces), porções, combos e bebidas, com busca e destaques.
- **Montar o lanche:**
  - adicionais pagos;
  - xis "aberto no prato";
  - sabor do refri e do suco;
  - tirar ingredientes;
  - molhos (sachês).
- **Entrega ou retirada:** o local de entrega é marcado no mapa e a taxa é calculada pela distância.
- **Pagamento:** pelo site (Pix e cartão via Mercado Pago) ou na entrega/retirada (dinheiro com troco, Pix, débito, crédito).
- **WhatsApp:** o pedido vai para o WhatsApp da loja com um link para confirmar no painel.
- **"Meus pedidos":** mostra o status de cada pedido e tem o botão "Pedir de novo".
- **Horário automático:** a loja abre e fecha sozinha ("Fechado · abre hoje às 18h").
- **Painel do dono:**
  - pedidos em tempo real com alerta sonoro;
  - cardápio, opções e fotos;
  - horários, taxa de entrega e pagamentos;
  - segurança e 2FA.
- **SEO:**
  - título e descrição;
  - imagem de compartilhamento (WhatsApp/Instagram);
  - dados estruturados para o Google (restaurante, endereço, horário e cardápio com preços);
  - `sitemap.xml` e `robots.txt`;
  - instalável na tela inicial (PWA).
- **LGPD:** página `/privacidade`.

## Rodar no computador

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # preencha SECRET_KEY
.venv\Scripts\python -m flask --app run criar-admin dono
.venv\Scripts\python run.py
```

- Site: http://127.0.0.1:5000
- Painel: http://127.0.0.1:5000/admin. A senha temporária aparece no comando `criar-admin` e precisa ser trocada no primeiro acesso.

Para voltar ao cardápio padrão da loja (os pedidos antigos são mantidos), use `flask --app run recarregar-cardapio`.

## Testes automáticos e monitoramento

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

- Os testes (pasta `tests/`) conferem preços, taxa de entrega, horários, criação de pedido, pagamentos em modo simulação, painel do dono, caixa, relatórios e as páginas públicas. Cada teste usa um banco novo e nenhum acessa a internet.
- **No GitHub:** o workflow `testes` roda os testes a cada push e pull request (aba **Actions**).
- **Monitor:** o workflow `monitor` confere se `https://numelhor.onrender.com/saude` e a página inicial estão no ar no horário da loja: de terça a domingo às 17h, 19h, 21h e 23h (de 1 hora antes de abrir até 1 hora depois de fechar). Se o horário da loja mudar, ajuste o agendamento em `.github/workflows/monitor.yml`. Se o site cair, o GitHub manda um e-mail. Para conferir na hora: aba **Actions → monitor → Run workflow**.

## Abrir pelo GitHub (Codespaces): jeito mais rápido para demonstrar

1. No repositório, clique em **Code → aba Codespaces → Create codespace on main**.
2. Espere de 2 a 4 minutos. O site liga sozinho e abre o arquivo `LINK-E-SENHA.txt`, com o link e a senha temporária do painel.
3. Para o cliente abrir o link: na aba **PORTAS/PORTS** (parte de baixo da tela), clique com o botão direito na porta **5000** e escolha **Visibilidade da porta → Público**.
4. Depois de ~30 min sem uso, o Codespace desliga sozinho. Para religar: github.com/codespaces → clique no NuMelhor. O site liga de novo e mantém pedidos, configurações e senha.

> Se o Codespace for **excluído**, os dados dele (pedidos, senha do painel, Mercado Pago conectado) são apagados. O código continua no GitHub.

## Publicar (Render + GitHub)

1. Suba este repositório no GitHub como **privado**.
2. No [Render](https://render.com), escolha **New → Blueprint** e aponte para o repositório. O arquivo `render.yaml` já configura tudo.
3. Preencha no Render `ADMIN_PASSWORD`: a senha do painel (10+ caracteres, misturando maiúsculas, minúsculas e números). Para trocá-la depois, altere esse valor no Render: o site reinicia com a senha nova. O endereço do site é detectado sozinho. Se usar domínio próprio, defina `PUBLIC_BASE_URL`.
4. Acesse `/admin`, entre com `dono` e a senha inicial, crie a senha definitiva e ative o 2FA em **Segurança**.

> No plano grátis do Render os dados (pedidos e configurações) **são apagados a cada reinício ou nova publicação**. Serve para demonstração. Para uso real, use um plano com disco permanente ou um servidor próprio, apontando `DATABASE_PATH` e `UPLOAD_DIR` para o disco, e faça backup diário.

## Pagamentos (Mercado Pago)

No painel, menu **Pagamentos**, tem o passo a passo completo:
1. criar a conta;
2. cadastrar a chave Pix nela;
3. criar a aplicação;
4. colar o Access Token (teste e depois produção);
5. configurar o webhook.

Sem conta conectada, o site fica em **modo simulação**, sem nenhum dinheiro real.

## Segurança

- Preços, taxa de entrega e total são calculados **só no servidor**. O navegador manda apenas os IDs.
- Dados de cartão nunca passam pelo site: o cartão é digitado na página do Mercado Pago.
- Um pagamento só é aceito depois de consultado na API do Mercado Pago (confere o pedido e o valor). O webhook tem a assinatura validada.
- O token do Mercado Pago fica **criptografado** no banco (chave derivada do `SECRET_KEY`). Trocar a conta exige senha, e em produção também o 2FA. Toda troca fica no registro de segurança.
- Painel do dono:
  - senha forte e troca obrigatória no 1º acesso;
  - 2FA;
  - bloqueio após várias tentativas erradas;
  - sessão expira se ficar parada;
  - botão "sair de todos os aparelhos".
- Proteção contra formulários forjados (CSRF), cabeçalhos de segurança (CSP, HSTS, anti-iframe), limite de pedidos por aparelho e validação das fotos enviadas.
- `.env` e `instance/` (banco, fotos enviadas) **não vão para o Git**.
- Verificado com `bandit` (análise do código) e `pip-audit` (bibliotecas com falhas conhecidas): nenhum problema.
