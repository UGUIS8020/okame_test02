import boto3
import os
from dotenv import load_dotenv

load_dotenv()

def init_tables():
    # AWS認証情報を設定
    dynamodb = boto3.client('dynamodb',
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )

    table_name = os.getenv("TABLE_NAME")
    
    try:
        # メインのユーザーテーブルを作成
        response = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {
                    'AttributeName': 'user_id',
                    'KeyType': 'HASH'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'user_id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'email',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'email-index',
                    'KeySchema': [
                        {
                            'AttributeName': 'email',
                            'KeyType': 'HASH'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 1,
                        'WriteCapacityUnits': 1
                    }
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 1,
                'WriteCapacityUnits': 1
            }
        )
        print(f"Created table {table_name}")
        
        # テーブルが作成されるのを待つ
        waiter = dynamodb.get_waiter('table_exists')
        waiter.wait(TableName=table_name)
        print(f"Table {table_name} is ready")

    except dynamodb.exceptions.ResourceInUseException:
        print(f"Table {table_name} already exists")
        
        try:
            # 既存のテーブルにインデックスが無い場合は追加
            response = dynamodb.describe_table(TableName=table_name)
            existing_indexes = response['Table'].get('GlobalSecondaryIndexes', [])
            
            if not any(index['IndexName'] == 'email-index' for index in existing_indexes):
                print("Adding email-index to existing table")
                dynamodb.update_table(
                    TableName=table_name,
                    AttributeDefinitions=[
                        {
                            'AttributeName': 'email',
                            'AttributeType': 'S'
                        }
                    ],
                    GlobalSecondaryIndexUpdates=[
                        {
                            'Create': {
                                'IndexName': 'email-index',
                                'KeySchema': [
                                    {
                                        'AttributeName': 'email',
                                        'KeyType': 'HASH'
                                    }
                                ],
                                'Projection': {
                                    'ProjectionType': 'ALL'
                                },
                                'ProvisionedThroughput': {
                                    'ReadCapacityUnits': 1,
                                    'WriteCapacityUnits': 1
                                }
                            }
                        }
                    ]
                )
                print("Added email-index successfully")
        except Exception as e:
            print(f"Error updating table: {str(e)}")
            raise
                
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        raise

if __name__ == '__main__':
    try:
        init_tables()
        print("Database initialization completed successfully")
    except Exception as e:
        print(f"Error: {str(e)}")