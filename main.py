import yfinance as yf
import requests
import pandas as pd
import time
import json
import os
from datetime import datetime, timedelta, timezone
import sys

# --- CONFIGURAÇÕES PESSOAIS ---
# Tenta pegar dos Segredos do GitHub, se não achar, usa o fixo (fallback)
TOKEN = os.environ.get("TOKEN", "8441643366:AAGIlgFg0Vr2oyuHdz39HH-S6XgaBCBgBSA")
CHAT_ID = os.environ.get("CHAT_ID", "918964322")

# Arquivos de Memória
ARQUIVO_MEMORIA = "memoria_alertas.json"
ARQUIVO_LOG = "historico_robo.txt"

# LISTA DE ATIVOS
ATIVOS = [
    # Índices e Brasil
    "^BVSP", "BOVA11.SA", "AFHI11.SA", "CDII11.SA", "GGRC11.SA", 
    "HFOF11.SA", "HGLG11.SA", "JURO11.SA", "KFOF11.SA", "KNCR11.SA", 
    "MXRF11.SA", "PVBI11.SA", "RBHG11.SA", "RBVA11.SA", "RECT11.SA", 
    "TRXF11.SA", "VGIR11.SA", "XPML11.SA",
    
    # EUA (Stocks/REITs/ETFs)
    "ARE", "EPR", "EQIX", "GLPI", "IAUM", "IBIT", "JEPI", "NOBL", 
    "O", "OHI", "PSA", "QQQ", "MCD", "REXR", "RING", "SCHD", 
    "SHV", "STAG", "VICI", "VNQ", "VOO",
    
    # Cripto (Roda 24/7)
    "BTC-USD"
]

# CONFIGURAÇÕES
TEMPO_ESPERA_HORAS = 2       # Evita spam repetido por 2 horas
PORCENTAGEM_EXTRA = 0.02     # 2% para alerta crítico

# Variáveis Globais de Controle
memoria_alertas = {}
alteracao_memoria = False # Flag para saber se precisamos salvar o JSON no final

# --- FUNÇÕES DE PERSISTÊNCIA (MEMÓRIA) ---
def carregar_memoria():
    """Lê o arquivo JSON para recuperar o histórico de envios."""
    global memoria_alertas
    if os.path.exists(ARQUIVO_MEMORIA):
        try:
            with open(ARQUIVO_MEMORIA, "r") as f:
                memoria_alertas = json.load(f)
        except Exception as e:
            print(f"Erro ao carregar memória: {e}")
            memoria_alertas = {}
    else:
        memoria_alertas = {}

def salvar_memoria_arquivo():
    """Salva a memória atualizada no arquivo JSON."""
    try:
        with open(ARQUIVO_MEMORIA, "w") as f:
            json.dump(memoria_alertas, f, indent=4)
        print("Memória salva com sucesso.")
    except Exception as e:
        print(f"Erro ao salvar memória: {e}")

# --- FUNÇÕES DE DATA E LOG ---
def pegar_hora_brasil():
    """Converte hora UTC para Brasília (UTC-3)"""
    return datetime.now(timezone.utc) - timedelta(hours=3)

def registrar_log(mensagem):
    """Escreve a mensagem na tela E num arquivo de texto"""
    agora = pegar_hora_brasil()
    texto_formatado = f"[{agora.strftime('%d/%m %H:%M')}] {mensagem}"
    
    # 1. Tela
    print(texto_formatado)
    sys.stdout.flush()
    
    # 2. Arquivo
    try:
        with open(ARQUIVO_LOG, "a", encoding="utf-8") as arquivo:
            arquivo.write(texto_formatado + "\n")
    except Exception as e:
        print(f"Erro ao salvar log: {e}")

def enviar_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": msg}
        requests.post(url, json=payload)
    except Exception as e:
        registrar_log(f"Erro Telegram: {e}")

def pode_enviar_msg(chave_unica):
    """Verifica no arquivo JSON se já enviou mensagem recentemente."""
    global memoria_alertas, alteracao_memoria
    agora = pegar_hora_brasil()
    
    if chave_unica in memoria_alertas:
        ultimo_envio_str = memoria_alertas[chave_unica]
        # Converte string ISO de volta para datetime
        ultimo_envio = datetime.fromisoformat(ultimo_envio_str)
        
        if (agora - ultimo_envio) < timedelta(hours=TEMPO_ESPERA_HORAS):
            return False # Ainda está no período de espera
            
    # Se passou do tempo ou nunca enviou:
    memoria_alertas[chave_unica] = agora.isoformat() # Salva como string
    alteracao_memoria = True # Marca para salvar no final
    return True

def checar_resumo_diario():
    # Nota: No GitHub Actions, como o script roda e para, 
    # usamos a memória JSON para controlar o resumo também.
    global memoria_alertas, alteracao_memoria
    
    agora = pegar_hora_brasil()
    hora_atual = agora.hour
    
    # Resumo às 09h e 18h
    if hora_atual in [9, 18]:
        chave_resumo = f"RESUMO_{agora.date()}_{hora_atual}"
        
        # Verifica na memória se já enviou esse resumo hoje
        if chave_resumo not in memoria_alertas:
            dia_semana = agora.weekday()
            status_mercado = "Mercado Fechado (FDS)" if dia_semana > 4 else "Mercado Aberto"
            
            msg = (f"🤖 STATUS ONLINE\n"
                   f"📅 {agora.strftime('%d/%m %H:%M')}\n"
                   f"ℹ️ {status_mercado}\n"
                   f"👁️ Monitorando {len(ATIVOS)} ativos.")
            enviar_telegram(msg)
            registrar_log("Resumo diário enviado.")
            
            # Atualiza memória
            memoria_alertas[chave_resumo] = agora.isoformat()
            alteracao_memoria = True

def calcular_ifr(series, periodo=14):
    delta = series.diff()
    ganho = delta.where(delta > 0, 0)
    perda = -delta.where(delta < 0, 0)
    media_ganho = ganho.ewm(alpha=1/periodo, adjust=False).mean()
    media_perda = perda.ewm(alpha=1/periodo, adjust=False).mean()
    rs = media_ganho / media_perda
    return 100 - (100 / (1 + rs))

def analisar_ativo(ativo):
    global alteracao_memoria
    agora = pegar_hora_brasil()
    dia_semana = agora.weekday()
    eh_final_de_semana = dia_semana >= 5
    eh_cripto = "-USD" in ativo
    
    if eh_final_de_semana and not eh_cripto: return
    
    try:
        # Download dos dados
        df = yf.download(ativo, period="6mo", interval="1d", progress=False, timeout=30, auto_adjust=False)
        
        if df.empty: return

        # --- TRATAMENTO DE DADOS ---
        series_preco = None
        
        # Tratamento para MultiIndex (mudança recente do yfinance)
        if isinstance(df.columns, pd.MultiIndex):
            try:
                # Tenta pegar apenas o ticker atual
                df_ativo = df.xs(ativo, axis=1, level=1)
            except:
                df_ativo = df
            
            # Procura coluna Close ou Adj Close
            for col in ['Close', 'Adj Close']:
                if col in df_ativo.columns:
                    series_preco = df_ativo[col]
                    break
            if series_preco is None: series_preco = df_ativo.iloc[:, 0]
        else:
            series_preco = df['Close'] if 'Close' in df else df.iloc[:, 0]

        series_preco = series_preco.dropna()
        if len(series_preco) < 30: return

        # Cálculos (Isso gera Séries/Listas)
        media = series_preco.rolling(window=20).mean()
        desvio = series_preco.rolling(window=20).std()
        
        # Bandas (Ainda são séries aqui)
        series_sup = media + (2 * desvio)
        series_inf = media - (2 * desvio)
        series_ifr = calcular_ifr(series_preco)

        # --- EXTRAÇÃO SEGURA DE VALORES ---
        try:
            preco = float(series_preco.iloc[-1])
            banda_sup = float(series_sup.iloc[-1])
            banda_inf = float(series_inf.iloc[-1])
            ifr_atual = float(series_ifr.iloc[-1])
        except Exception as err_conv:
            registrar_log(f"Erro conversão valores {ativo}: {err_conv}")
            return

        # Níveis Críticos
        sup_crit = banda_sup * (1 + PORCENTAGEM_EXTRA)
        inf_crit = banda_inf * (1 - PORCENTAGEM_EXTRA)

        # Log visual rápido (sem salvar em arquivo para não lotar, salva só no final se tiver msg)
        #print(f"🔎 {ativo}: ${preco:.2f} (RSI: {ifr_atual:.0f})")
        registrar_log(f"🔎 {ativo}: ${preco:.2f} (RSI: {ifr_atual:.0f})")

        # Mensagens
        msg = ""
        tipo = ""
        status_ifr = ""
        
        if ifr_atual > 70: status_ifr = "🔥(Sobrecompra)"
        elif ifr_atual < 30: status_ifr = "❄️(Sobrevenda)"

        if preco >= sup_crit:
            tipo = "VENDA_CRIT"
            msg = (f"🚨 VENDA CRÍTICA: {ativo} 🚨\nEXPLODIU!\n💵 {preco:.2f}\n📈 Teto Crit: {sup_crit:.2f}\n⚡ IFR: {ifr_atual:.0f} {status_ifr}")
        elif preco >= banda_sup:
            tipo = "VENDA_NORM"
            msg = (f"⚠️ VENDA: {ativo}\nTocou Banda Sup\n💵 {preco:.2f}\n📈 Banda: {banda_sup:.2f}\n⚡ IFR: {ifr_atual:.0f} {status_ifr}")
        elif preco <= inf_crit:
            tipo = "COMPRA_CRIT"
            msg = (f"💎 COMPRA CRÍTICA: {ativo} 💎\nDESABOU!\n💵 {preco:.2f}\n📉 Piso Crit: {inf_crit:.2f}\n⚡ IFR: {ifr_atual:.0f} {status_ifr}")
        elif preco <= banda_inf:
            tipo = "COMPRA_NORM"
            msg = (f"✅ COMPRA: {ativo}\nTocou Banda Inf\n💵 {preco:.2f}\n📉 Banda: {banda_inf:.2f}\n⚡ IFR: {ifr_atual:.0f} {status_ifr}")

        # Se houver mensagem, verifica se pode enviar (memória)
        if msg and pode_enviar_msg(f"{ativo}_{tipo}"):
            registrar_log(f"⚡ ALERTA DISPARADO: {ativo}")
            enviar_telegram(msg)

    except Exception as e:
        registrar_log(f"Erro ao ler {ativo}: {e}")
        pass

# --- INÍCIO DA EXECUÇÃO (SINGLE PASS) ---
if __name__ == "__main__":
    #print("--- INICIANDO EXECUÇÃO (GITHUB ACTIONS) ---")
    
    # 1. Carrega a memória do arquivo JSON
    carregar_memoria()
    
    # 2. Checa resumo diário
    checar_resumo_diario()
    
    agora = pegar_hora_brasil()
    registrar_log(f"--- Ciclo {agora.strftime('%H:%M')} iniciado ---")
    
    # 3. Analisa todos os ativos
    for ativo in ATIVOS:
        analisar_ativo(ativo)
        time.sleep(1) # Pequena pausa para evitar bloqueio do Yahoo
    
    # 4. Salva a memória no arquivo JSON se houve alteração
    if alteracao_memoria:
        salvar_memoria_arquivo()
    else:
        print("Nenhum novo alerta enviado. Memória intacta.")
        
    #print("--- FIM DA EXECUÇÃO ---")
