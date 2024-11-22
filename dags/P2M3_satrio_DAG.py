import pandas as pd
from sqlalchemy import create_engine
from airflow import DAG
from airflow.decorators import task
from airflow.operators.empty import EmptyOperator
from datetime import datetime
from elasticsearch import Elasticsearch, helpers


default_args = {
    'owner': 'Satrio',
    'start_date': datetime(2024, 8, 11)
}

# Konfigurasi path dan database
local_csv_path = '/opt/airflow/data/P2M3_satrio_data_raw.csv'
postgres_url = "postgresql+psycopg2://airflow_user:airflow_password@postgres/data_from_airflow"
engine = create_engine(postgres_url)

with DAG(
    'etl_csv_file',
    description='ETL process for CSV to PostgreSQL',
    schedule_interval='30 6 * * *',  # Menjadwalkan eksekusi setiap jam 6:30
    default_args=default_args,
    catchup=False
) as dag:
    
    start = EmptyOperator(task_id='start')
    end = EmptyOperator(task_id='end')

    # Task untuk memasukkan data ke PostgreSQL
    @task()
    def insert_to_db():
        # Load data dari CSV
        df = pd.read_csv(local_csv_path)

        # Membuat koneksi ke database
        conn = engine.connect()

        # Menghapus tabel yang sudah ada jika ada
        conn.execute('DROP TABLE IF EXISTS adidas_sales_raw')

        # Menyisipkan data baru ke dalam PostgreSQL, dengan menggantikan tabel yang lama
        df.to_sql('adidas_sales_raw', conn, index=False, if_exists='replace')

        print("Data inserted into PostgreSQL")

        # Menutup koneksi setelah selesai
        conn.close()

    # Task untuk preprocessing data
    @task()
    def preprocess_data():
         # Load data langsung dari PostgreSQL
        df = pd.read_sql('SELECT * FROM adidas_sales_raw', engine)
        
        # Pembersihan data: Menghapus koma, simbol $, dan % pada kolom tertentu
        df['Price per Unit'] = df['Price per Unit'].replace({',': '', '$': ''}, regex=True).astype(float)
        df['Total Sales'] = df['Total Sales'].replace({',': '', '$': ''}, regex=True).astype(float)
        df['Operating Profit'] = df['Operating Profit'].replace({',': '', '$': ''}, regex=True).astype(float)
        df['Operating Margin'] = df['Operating Margin'].replace({',': '', '%': ''}, regex=True).astype(float)
        
        # Menangani missing values pada kolom Units Sold
        df['Units Sold'] = pd.to_numeric(df['Units Sold'], errors='coerce')  # Ganti nilai yang tidak bisa dikonversi menjadi NaN
        df['Units Sold'].fillna(0, inplace=True)  # Ganti NaN dengan 0
        
        # Tangani inf jika ada (mengganti dengan nilai yang valid)
        df['Units Sold'].replace([float('inf'), -float('inf')], 0, inplace=True)

        # Menangani missing values (misalnya dengan mengisi dengan rata-rata kolom)
        df.fillna(df.mean(numeric_only=True), inplace=True)

        # Normalisasi nama kolom
        df.columns = [col.strip().lower().replace(' ', '_').replace('|', '') for col in df.columns]

        # Menyimpan hasil preprocessing ke dalam file untuk kemudian dimasukkan ke Kibana
        df.to_csv('/opt/airflow/data/adidas_sales_preprocessed.csv', index=False)
        print("Data cleaned and preprocessed")

    # Task untuk load data ke Kibana
    @task()
    def send_data_to_elastich_search():
        # Connect to elasticsearch
        es = Elasticsearch([{'host': 'elasticsearch-m3', 'port': 9200, 'scheme': 'http'}])

         # Load cleaned data 
        df = pd.read_csv('/opt/airflow/data/adidas_sales_preprocessed.csv')

        docs = df.to_dict(orient='records')

        actions = [
            {
                "_index": "frompostgresql_1",
                "_type": "doc",
                "_source": doc
            }
            for doc in docs
        ]

        helpers.bulk(es, actions)

    # Menentukan alur eksekusi task
    start >> insert_to_db() >> preprocess_data() >> send_data_to_elastich_search() >> end
