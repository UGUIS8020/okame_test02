import boto3
from datetime import datetime, date
import os
from dotenv import load_dotenv
from flask import Flask
import json
from botocore.exceptions import ClientError
import uuid
from typing import Optional, Dict, Any
from werkzeug.security import generate_password_hash

def create_app():
    app = Flask(__name__)
    load_dotenv()
    
    # AWS DynamoDBリソースの設定
    app.dynamodb = boto3.resource('dynamodb',
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "ap-northeast-1")
    )
    
    # テーブル名の設定
    environment = os.getenv("ENVIRONMENT")
    app.table_name = f"{environment}-users"    
    app.table = app.dynamodb.Table(app.table_name)
    
    return app

def generate_user_id(prefix: str = "user") -> str:
    """UUIDベースのユーザーID生成"""
    return f"{prefix}_{str(uuid.uuid4())}"

def create_user_table_if_not_exists(app):
    """ユーザーテーブルが存在しない場合は作成する"""
    try:
        # テーブルの存在確認
        app.table.table_status
        print(f"テーブル {app.table_name} は既に存在します")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            # テーブルが存在しない場合は作成
            table = app.dynamodb.create_table(
                TableName=app.table_name,
                KeySchema=[
                    {
                        'AttributeName': 'user_id',
                        'KeyType': 'HASH'  # Partition key
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
                    },
                    {
                        'AttributeName': 'organization',
                        'AttributeType': 'S'
                    },
                    {
                        'AttributeName': 'created_at',
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
                        }
                    },
                    {
                        'IndexName': 'organization-index',
                        'KeySchema': [
                            {
                                'AttributeName': 'organization',
                                'KeyType': 'HASH'
                            },
                            {
                                'AttributeName': 'created_at',
                                'KeyType': 'RANGE'
                            }
                        ],
                        'Projection': {
                            'ProjectionType': 'ALL'
                        }
                    }
                ],
                BillingMode='PAY_PER_REQUEST'
            )
            # テーブルが作成されるまで待機
            table.wait_until_exists()
            print(f"テーブル {app.table_name} が作成されました")
            return table
        else:
            raise e

def date_to_iso(d):
    """date型をISO形式の文字列に変換"""
    if isinstance(d, date):
        return d.isoformat()
    return d

def get_user_by_email(app, email: str) -> Optional[Dict[str, Any]]:
    """メールアドレスによるユーザー検索（重複チェック用）"""
    try:
        response = app.table.query(
            IndexName='email-index',
            KeyConditionExpression='email = :email',
            ExpressionAttributeValues={
                ':email': email
            }
        )
        items = response.get('Items', [])
        return items[0] if items else None
    except Exception as e:
        print(f"ユーザー検索エラー: {str(e)}")
        return None

def create_user(app, user_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """新規ユーザーを作成する"""
    try:
        # テーブルの存在確認と作成
        create_user_table_if_not_exists(app)
        
        # 必須フィールドの確認
        required_fields = ['email', 'password', 'user_name']
        if not all(field in user_data for field in required_fields):
            raise ValueError("必須フィールドが不足しています")
        
        # メールアドレスの重複チェック
        existing_user = get_user_by_email(app, user_data['email'])
        if existing_user:
            raise ValueError("このメールアドレスは既に使用されています")
        
        # 現在時刻
        current_time = datetime.now().isoformat()
        
        # ユーザーデータの準備
        new_user = {
            'user_id': generate_user_id(),
            'organization': user_data.get('organization', 'uguis'),  # デフォルトはuguis
            'email': user_data['email'],
            'password': generate_password_hash(user_data['password']),
            'display_name': user_data.get('display_name', user_data['user_name']),
            'user_name': user_data['user_name'],
            'furigana': user_data.get('furigana', ''),
            'gender': user_data.get('gender', ''),
            'date_of_birth': date_to_iso(user_data.get('date_of_birth')),
            'post_code': user_data.get('post_code', ''),
            'address': user_data.get('address', ''),
            'phone': user_data.get('phone', ''),
            'administrator': user_data.get('administrator', False),
            'created_at': current_time,
            'updated_at': current_time
        }
        
        # ユーザーデータの保存
        app.table.put_item(Item=new_user)
        
        # パスワードを除外してユーザー情報を返す
        new_user.pop('password', None)
        return new_user
        
    except Exception as e:
        print(f"ユーザー作成エラー: {str(e)}")
        raise

def create_test_user(app):
    """テストユーザーを作成する"""
    test_data = {
        'organization': 'てすとくん01',  # 明示的に所属を設定
        'email': 'test01@test.com',
        'password': '00000000',
        'user_name': '山田　太郎',
        'display_name': '渋谷',
        'furigana': 'ヤマダ　タロウ',
        'gender': 'male',
        'date_of_birth': date(1988, 11, 10),
        'post_code': '3430032',
        'address': '埼玉県越谷市袋山95-1',
        'phone': '09000000000',
        'administrator': False
    }
    
    try:
        created_user = create_user(app, test_data)
        print("テストユーザーが作成されました:")
        print(json.dumps(created_user, ensure_ascii=False, indent=2))
        return created_user
    except Exception as e:
        print(f"テストユーザー作成エラー: {str(e)}")
        return None

if __name__ == "__main__":
    app = create_app()
    
    # テストユーザーの作成
    user = create_test_user(app)
    
    if user:
        print("\nテストユーザーが正常に作成されました")
        print(f"ユーザーID: {user['user_id']}")
        print(f"所属: {user['organization']}")