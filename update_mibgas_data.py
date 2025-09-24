# Ficheiro: update_mibgas_data.py
# Ficheiro: update_data.py

import pandas as pd
import requests
import numpy as np
import datetime
from calendar import monthrange
from io import BytesIO
import time
import re
import warnings
import os

# Ignorar avisos de SSL quando verify=False é usado
from requests.packages.urllib3.exceptions import InsecureRequestWarning
warnings.simplefilter('ignore', InsecureRequestWarning)


def fetch_mibgas_spot_data():
    """
    Obtém os preços diários (spot) da aba "MIBGAS Indexes" dos ficheiros anuais.
    """
    print("A procurar dados SPOT MIBGAS (da aba MIBGAS Indexes)...")
    dataframes_anuais = []
    
    ano_atual = datetime.date.today().year
    anos_para_buscar = [ano_atual - 1, ano_atual] 

    for ano in anos_para_buscar:
        url = f"https://www.mibgas.es/pt/file-access/MIBGAS_Data_{ano}.xlsx?path=AGNO_{ano}/XLS"
        
        try:
            print(f"  > A tentar descarregar o ficheiro para o ano {ano}...")
            response = requests.get(url, timeout=30, verify=False)
            response.raise_for_status()
            
            df_anual = pd.read_excel(BytesIO(response.content), sheet_name="MIBGAS Indexes")
            
            coluna_data = next((col for col in df_anual.columns if 'delivery day' in col.lower()), None)
            coluna_preco = next((col for col in df_anual.columns if 'last price index day-ahead' in col.lower()), None)

            if coluna_data and coluna_preco:
                df_filtrado = df_anual[[coluna_data, coluna_preco]].copy()
                df_filtrado.rename(columns={coluna_data: 'Data', coluna_preco: 'Preço'}, inplace=True)
                df_filtrado['Data'] = pd.to_datetime(df_filtrado['Data'], errors='coerce')
                df_filtrado.dropna(subset=['Data', 'Preço'], inplace=True)
                dataframes_anuais.append(df_filtrado)
                print(f"  > Sucesso! Processados {len(df_filtrado)} registos de MIBGAS Indexes para {ano}.")
            else:
                print(f"  > Aviso: Nomes de coluna esperados não encontrados na aba 'MIBGAS Indexes' para o ano {ano}.")

        except Exception as e:
            print(f"  > Aviso: Não foi possível processar o ficheiro para o ano {ano}. Erro: {e}")
            continue
            
    if not dataframes_anuais:
        print("Nenhum dado SPOT encontrado.")
        return pd.DataFrame(columns=['Data', 'Preço'])
        
    df_completo = pd.concat(dataframes_anuais, ignore_index=True)
    df_completo.drop_duplicates(subset=['Data'], keep='last', inplace=True)
    
    return df_completo


def fetch_omip_gas_futures_data():
    """
    Obtém e processa os preços de futuros de gás do OMIP.
    Prioriza a coluna de preços 'D' e usa 'D-1' como fallback.
    """
    print("A procurar preços FUTUROS de gás no OMIP...")
    
    today = datetime.date.today()
    all_processed_futures = []

    for i in range(7):
        current_date = today - datetime.timedelta(days=i)
        date_str = current_date.strftime('%Y-%m-%d')
        url_omip = f"https://www.omip.pt/pt/dados-mercado?date={date_str}&product=NG&zone=ES&instrument=FGE"
        
        try:
            print(f"  > A tentar obter dados para a data: {date_str}...")
            list_of_tables = pd.read_html(url_omip, header=[0, 1])
            
            if list_of_tables:
                print(f"  > Sucesso! Encontradas {len(list_of_tables)} tabelas com dados para {date_str}.")
                
                for df_table in list_of_tables:
                    if df_table.empty: continue
                    try:
                        df_table.columns = ['_'.join(map(str, col)).strip() for col in df_table.columns.values]
                        
                        coluna_produto = next((col for col in df_table.columns if 'contract name' in col.lower()), None)
                        coluna_preco_d = next((col for col in df_table.columns if 'reference prices_d' in col.lower() and 'd-1' not in col.lower()), None)
                        coluna_preco_d1 = next((col for col in df_table.columns if 'reference prices_d-1' in col.lower()), None)
                        
                        if not coluna_produto or (not coluna_preco_d and not coluna_preco_d1): continue

                        for _, row in df_table.iterrows():
                            if not pd.notna(row[coluna_produto]): continue
                            price_value = None
                            if coluna_preco_d and pd.notna(row[coluna_preco_d]):
                                try: price_value = float(str(row[coluna_preco_d]).replace(',', '.'))
                                except (ValueError, TypeError): price_value = None
                            if price_value is None and coluna_preco_d1 and pd.notna(row[coluna_preco_d1]):
                                try: price_value = float(str(row[coluna_preco_d1]).replace(',', '.'))
                                except (ValueError, TypeError): price_value = None
                            if price_value is not None:
                                parsed_info = parse_omip_product_name(row[coluna_produto], today)
                                if parsed_info:
                                    all_processed_futures.append({'start_date': parsed_info['start'], 'end_date': parsed_info['end'], 'price': price_value, 'priority': parsed_info['priority']})
                    except Exception as e:
                        print(f"  > Aviso: Erro ao processar uma das tabelas. Erro: {e}")
                        continue
                if all_processed_futures: break
        except Exception as e:
            print(f"  > Não foram encontrados dados para {date_str}. Tentando o dia anterior...")
            continue
    
    if not all_processed_futures:
        print("  > Nenhuma tabela de futuros encontrada no OMIP nos últimos 7 dias.")
        return []
    
    print(f"  > Total de {len(all_processed_futures)} produtos de futuros encontrados e processados em todas as tabelas.")
    return all_processed_futures

    
def parse_omip_product_name(product_name, today):
    month_map = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
    product_name = str(product_name).strip()
    try:
        match = re.search(r'FGE\s+D\s+\w+(\d{1,2})(\w{3})-(\d{2})', product_name)
        if match:
            day, month_str, year_short = match.groups(); month = month_map[month_str.capitalize()]; year = 2000 + int(year_short)
            date = datetime.date(year, month, int(day)); return {'start': date, 'end': date, 'priority': 1}
        match = re.search(r'FGE\s+WE\s+(\d{1,2})(\w{3})-(\d{2})', product_name)
        if match:
            day, month_str, year_short = match.groups(); month = month_map[month_str.capitalize()]; year = 2000 + int(year_short)
            saturday = datetime.date(year, month, int(day)); sunday = saturday + datetime.timedelta(days=1); return {'start': saturday, 'end': sunday, 'priority': 2}
        match = re.search(r'FGE\s+WkDs(\d{1,2})-(\d{2})', product_name)
        if match:
            week, year_short = match.groups(); year = 2000 + int(year_short)
            start_date = datetime.datetime.strptime(f'{year}-W{int(week)-1}-1', "%Y-W%W-%w").date(); end_date = start_date + datetime.timedelta(days=4); return {'start': start_date, 'end': end_date, 'priority': 3}
        match = re.search(r'FGE\s+M\s+(\w{3})-(\d{2})', product_name)
        if match:
            month_str, year_short = match.groups(); month = month_map[month_str.capitalize()]; year = 2000 + int(year_short)
            start_date = datetime.date(year, month, 1); end_date = start_date.replace(day=monthrange(year, month)[1]); return {'start': start_date, 'end': end_date, 'priority': 4}
        match = re.search(r'FGE\s+Q(\d)-(\d{2})', product_name)
        if match:
            quarter, year_short = match.groups(); year = 2000 + int(year_short); start_month = (int(quarter) - 1) * 3 + 1; end_month = start_month + 2
            start_date = datetime.date(year, start_month, 1); end_date = start_date.replace(month=end_month, day=monthrange(year, end_month)[1]); return {'start': start_date, 'end': end_date, 'priority': 5}
        match = re.search(r'FGE\s+(Win|Sum)-(\d{2})', product_name)
        if match:
            season, year_short = match.groups(); year = 2000 + int(year_short)
            if season == 'Win': start_date = datetime.date(year, 10, 1); end_date = datetime.date(year + 1, 3, 31)
            else: start_date = datetime.date(year, 4, 1); end_date = datetime.date(year, 9, 30)
            return {'start': start_date, 'end': end_date, 'priority': 6}
        match = re.search(r'FGE\s+YR-(\d{2})', product_name)
        if match:
            year_short = match.groups()[0]; year = 2000 + int(year_short)
            start_date = datetime.date(year, 1, 1); end_date = datetime.date(year, 12, 31); return {'start': start_date, 'end': end_date, 'priority': 7}
    except (ValueError, KeyError, IndexError) as e:
        print(f"  > Aviso: Não foi possível interpretar o produto OMIP '{product_name}'. Erro: {e}")
        return None
    return None


def criar_dataframe_mibgas_completo():
    today = datetime.date.today(); start_of_year = datetime.date(2024, 1, 1); end_of_next_year = datetime.date(today.year + 2, 12, 31)
    df_spot = fetch_mibgas_spot_data()
    
    ultima_data_spot = None
    if not df_spot.empty: 
        df_spot['Data'] = pd.to_datetime(df_spot['Data']).dt.date
        ultima_data_spot = df_spot['Data'].max()

    futures_list = fetch_omip_gas_futures_data()
    date_range = pd.to_datetime(pd.date_range(start=start_of_year, end=end_of_next_year, freq='D')).date
    df_final = pd.DataFrame(date_range, columns=['Data']); df_final['Preço'] = np.nan
    if not df_spot.empty:
        df_final = df_final.merge(df_spot, on='Data', how='left', suffixes=('_x', '_y'))
        df_final['Preço'] = df_final['Preço_y'].fillna(df_final['Preço_x'])
        df_final.drop(columns=['Preço_x', 'Preço_y'], inplace=True)
        
    futures_list_sorted = sorted(futures_list, key=lambda x: x['priority'])
    
    for future in futures_list_sorted:
        mask = (df_final['Data'] >= future['start_date']) & (df_final['Data'] <= future['end_date'])
        df_final.loc[mask & df_final['Preço'].isna(), 'Preço'] = future['price']
        
    df_final['Preço'] = df_final['Preço'].ffill().bfill()
    print("Série temporal de preços MIBGAS criada com sucesso.")
    
    return df_final, ultima_data_spot


# --- Bloco Principal de Execução ---
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    EXCEL_FILE_PATH = os.path.join(script_dir, 'MIBGAS.xlsx')
    
    print(f"Iniciando a automação de dados MIBGAS... Ficheiro alvo: {EXCEL_FILE_PATH}")
    gwdes_df_novo, ultima_data_spot = criar_dataframe_mibgas_completo()
    
    if not gwdes_df_novo.empty:
        info_df = None
        if ultima_data_spot:
            info_df = pd.DataFrame({'Descricao': ['Ultima Data MIBGAS SPOT'], 'Data': [ultima_data_spot]})
            print(f"Última data SPOT encontrada para registar: {ultima_data_spot.strftime('%Y-%m-%d')}")

        try:
            with pd.ExcelWriter(EXCEL_FILE_PATH, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                gwdes_df_novo.to_excel(writer, sheet_name='GWDES', index=False)
                if info_df is not None:
                    info_df.to_excel(writer, sheet_name='Info', index=False)
            print(f"\n✅ Ficheiro '{os.path.basename(EXCEL_FILE_PATH)}' atualizado com sucesso nas abas 'GWDES' e 'Info'.")
        
        except FileNotFoundError:
            print(f"\n⚠️ Aviso: O ficheiro '{os.path.basename(EXCEL_FILE_PATH)}' não foi encontrado. A criar um novo ficheiro...")
            try:
                with pd.ExcelWriter(EXCEL_FILE_PATH, engine='openpyxl') as writer:
                    gwdes_df_novo.to_excel(writer, sheet_name='GWDES', index=False)
                    if info_df is not None:
                        info_df.to_excel(writer, sheet_name='Info', index=False)
                print(f"\n✅ Ficheiro '{os.path.basename(EXCEL_FILE_PATH)}' criado com sucesso com as abas 'GWDES' e 'Info'.")
            except Exception as e:
                print(f"\n❌ ERRO ao criar o novo ficheiro Excel: {e}")
        except Exception as e:
            print(f"\n❌ ERRO ao escrever no ficheiro Excel: {e}")
    else:
        print("\n⚠️ Aviso: Nenhum dado foi gerado, o ficheiro Excel não foi modificado.")