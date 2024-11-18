from flask import Flask
from flask_wtf import FlaskForm
from flask import render_template, request, redirect, url_for, flash, abort, session
from flask_login import UserMixin, LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from wtforms import ValidationError, StringField, PasswordField, SubmitField, SelectField, DateField, BooleanField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional
import pytz
import os
import boto3
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime, date
import io
from PIL import Image
from dateutil.relativedelta import relativedelta
from botocore.exceptions import ClientError
from init_db import init_tables  # init_counter_tableから変更
import logging
import time
import random
from urllib.parse import urlparse, urljoin
from dotenv import load_dotenv

# ロギングの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# グローバル変数の定義
app = Flask(__name__)
login_manager = LoginManager()

def create_app():
    """アプリケーションの初期化と設定"""
    try:
        # 環境変数の読み込み
        load_dotenv()

        # シークレットキーの設定
        app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24))
        
        # AWS S3の設定
        app.config['S3_BUCKET'] = os.getenv("S3_BUCKET")
        aws_region = os.getenv("AWS_REGION", "ap-northeast-1")
        app.config['S3_LOCATION'] = f"https://{app.config['S3_BUCKET']}.s3.{aws_region}.amazonaws.com/"
        
        # AWS認証情報
        aws_credentials = {
            'aws_access_key_id': os.getenv("AWS_ACCESS_KEY_ID"),
            'aws_secret_access_key': os.getenv("AWS_SECRET_ACCESS_KEY"),
            'region_name': aws_region
        }
        
        # 必須環境変数のチェック
        required_env_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET"]
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        # AWSクライアントの初期化
        app.s3 = boto3.client('s3', **aws_credentials)
        app.dynamodb = boto3.client('dynamodb', **aws_credentials)
        app.dynamodb_resource = boto3.resource('dynamodb', **aws_credentials)
        
        # テーブル名の設定
        environment = os.getenv("ENVIRONMENT", "dev")
        app.table_name = f"{environment}-users"
        app.table = app.dynamodb_resource.Table(app.table_name)

        # Flask-Loginの設定
        login_manager.init_app(app)
        login_manager.login_view = 'login'
        login_manager.login_message = 'このページにアクセスするにはログインが必要です。'
        
        # DynamoDBテーブルの初期化
        init_tables()
        logger.info("Application initialized successfully")
        
        return app
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        raise

app = create_app()  # アプリケーションの初期化

def tokyo_time():
    return datetime.now(pytz.timezone('Asia/Tokyo'))

@login_manager.user_loader
def load_user(user_id):
    try:
        response = app.dynamodb.get_item(
            TableName=app.table_name,
            Key={'user_id': {'S': user_id}}
        )
        if 'Item' in response:
            user_data = response['Item']
            return User(
                user_id=user_data['user_id']['S'],
                display_name=user_data['display_name']['S'],
                user_name=user_data['user_name']['S'],
                furigana=user_data['furigana']['S'],
                email=user_data['email']['S'],
                password_hash=user_data['password']['S'],
                gender=user_data['gender']['S'],
                date_of_birth=user_data['date_of_birth']['S'],
                post_code=user_data['post_code']['S'],
                address=user_data['address']['S'],
                phone=user_data['phone']['S']
            )
        return None
    except Exception as e:
        app.logger.error(f"Error loading user: {str(e)}")
        return None

class RegistrationForm(FlaskForm):
    organization = SelectField('所属', choices=[('uguis', '鶯'),('other', 'その他')], default='uguis', validators=[DataRequired(message='所属を選択してください')])
    display_name = StringField('表示ネーム LINE名など', validators=[DataRequired(message='表示名を入力してください'), Length(min=3, max=30, message='表示名は3文字以上30文字以下で入力してください')])
    user_name = StringField('ユーザー名', validators=[DataRequired()])
    furigana = StringField('フリガナ', validators=[DataRequired()])
    phone = StringField('電話番号', validators=[DataRequired(), Length(min=10, max=15, message='正しい電話番号を入力してください')])
    post_code = StringField('郵便番号', validators=[DataRequired(), Length(min=7, max=7, message='ハイフン無しで７桁で入力してください')])
    address = StringField('住所', validators=[DataRequired(), Length(max=100, message='住所は100文字以内で入力してください')])
    email = StringField('メールアドレス', validators=[DataRequired(), Email(message='正しいメールアドレスを入力してください')])
    email_confirm = StringField('メールアドレス確認', validators=[DataRequired(), Email(), EqualTo('email', message='メールアドレスが一致していません')])
    password = PasswordField('パスワード', validators=[DataRequired(), Length(min=8, message='パスワードは8文字以上で入力してください'), EqualTo('pass_confirm', message='パスワードが一致していません')])
    pass_confirm = PasswordField('パスワード(確認)', validators=[DataRequired()])    
    gender = SelectField('性別', choices=[('', '性別'), ('male', '男性'), ('female', '女性'), ('other', 'その他')], validators=[DataRequired()])
    date_of_birth = DateField('生年月日', format='%Y-%m-%d', validators=[DataRequired()])
    submit = SubmitField('登録')

    def validate_email(self, field):
        try:
            # emailのインデックスを使用して検索
            response = app.dynamodb.query(
                TableName=app.table_name,
                IndexName='email-index',
                KeyConditionExpression='email = :email',
                ExpressionAttributeValues={
                    ':email': {'S': field.data}
                }
            )
            if response.get('Items'):
                raise ValidationError('入力されたメールアドレスは既に登録されています。')
        except Exception as e:
            app.logger.error(f"Error validating email: {str(e)}")
            raise ValidationError('メールアドレスの確認中にエラーが発生しました。')
        
        
class UpdateUserForm(FlaskForm):
    organization = SelectField('所属', choices=[('uguis', '鶯'), ('other', 'その他')], validators=[DataRequired(message='所属を選択してください')])    
    display_name = StringField('表示ネーム LINE名など', validators=[DataRequired(), Length(min=3, max=30)])    
    user_name = StringField('ユーザー名', validators=[DataRequired()])    
    furigana = StringField('フリガナ',  validators=[DataRequired()])    
    phone = StringField('電話番号', validators=[DataRequired(), Length(min=10, max=15, message='正しい電話番号を入力してください')])    
    post_code = StringField('郵便番号', validators=[DataRequired(), Length(min=7, max=7, message='ハイフン無しで７桁で入力してください')])    
    address = StringField('住所', validators=[DataRequired(), Length(max=100, message='住所は100文字以内で入力してください')])    
    email = StringField('メールアドレス', validators=[DataRequired(), Email(message='正しいメールアドレスを入力してください')])    
    password = PasswordField('パスワード', validators=[Optional(),  # パスワード変更は任意
                                                  Length(min=8, message='パスワードは8文字以上で入力してください'),EqualTo('pass_confirm', message='パスワードが一致していません')])    
    pass_confirm = PasswordField('パスワード(確認)')    
    gender = SelectField('性別', choices=[('', '性別'), ('male', '男性'), ('female', '女性'), ('other', 'その他')], validators=[DataRequired()])    
    date_of_birth = DateField('生年月日', format='%Y-%m-%d', validators=[DataRequired()])    
    submit = SubmitField('更新')

    def __init__(self, user_id, dynamodb_table, *args, **kwargs):
        super(UpdateUserForm, self).__init__(*args, **kwargs)
        self.id = user_id
        self.table = dynamodb_table

    def validate_email(self, field):
        """メールアドレスの重複チェック（自分のメールアドレスは除外）"""
        try:
            response = self.table.query(
                IndexName='email-index',
                KeyConditionExpression='email = :email',
                ExpressionAttributeValues={
                    ':email': field.data
                }
            )
            
            # 検索結果があり、かつ自分以外のユーザーの場合はエラー
            if response.get('Items'):
                for item in response['Items']:
                    if item['user_id'] != self.id:
                        raise ValidationError('このメールアドレスは既に使用されています。')
                        
        except ClientError as e:
            raise ValidationError('メールアドレスの確認中にエラーが発生しました。')


class User(UserMixin):
    def __init__(self, user_id, display_name, user_name, furigana, email, password_hash, 
                 gender, date_of_birth, post_code, address, phone, 
                 organization='uguis', administrator=False, 
                 created_at=None, updated_at=None):
        self.user_id = user_id
        self.display_name = display_name
        self.user_name = user_name
        self.furigana = furigana
        self.email = email
        self.password_hash = password_hash  # ハッシュ化されたパスワードを保持
        self.gender = gender
        self.date_of_birth = date_of_birth
        self.post_code = post_code
        self.address = address
        self.phone = phone
        self.organization = organization
        self.administrator = administrator
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()    

    def get_id(self):
        return str(self.user_id)  # Flask-Loginは文字列のIDを期待します

    @staticmethod
    def from_dynamodb_item(item):
        """DynamoDBのアイテムからUserオブジェクトを生成"""
        raw_admin = item.get('administrator', {})
        print(f"Raw administrator data: {raw_admin}")
        admin_bool = raw_admin.get('BOOL', False)
        print(f"Administrator bool value: {admin_bool}, type: {type(admin_bool)}")

        return User(
            user_id=item.get('user_id', {}).get('S'),
            display_name=item.get('display_name', {}).get('S'),
            user_name=item.get('user_name', {}).get('S'),
            furigana=item.get('furigana', {}).get('S'),
            email=item.get('email', {}).get('S'),
            password_hash=item.get('password', {}).get('S'),  # パスワードハッシュを直接受け取る
            gender=item.get('gender', {}).get('S'),
            date_of_birth=item.get('date_of_birth', {}).get('S'),
            post_code=item.get('post_code', {}).get('S'),
            address=item.get('address', {}).get('S'),
            phone=item.get('phone', {}).get('S'),
            organization=item.get('organization', {}).get('S', 'uguis'),
            administrator=bool(item.get('administrator', {}).get('BOOL', False)),  # ブール型に変換
            created_at=item.get('created_at', {}).get('S'),
            updated_at=item.get('updated_at', {}).get('S')
        )

    def to_dynamodb_item(self):
        """UserオブジェクトをDynamoDBアイテムに変換"""
        return {
            "user_id": {"S": self.user_id},
            "organization": {"S": self.organization},
            "address": {"S": self.address},
            "administrator": {"BOOL": self.administrator},
            "created_at": {"S": self.created_at},
            "date_of_birth": {"S": self.date_of_birth if self.date_of_birth else ''},
            "display_name": {"S": self.display_name},
            "email": {"S": self.email},
            "furigana": {"S": self.furigana},
            "gender": {"S": self.gender},
            "password": {"S": self.password_hash},  # ハッシュ化されたパスワードを保存
            "phone": {"S": self.phone},
            "post_code": {"S": self.post_code},
            "updated_at": {"S": self.updated_at},
            "user_name": {"S": self.user_name}
        }

    def set_password(self, password):
        """パスワードをハッシュ化して設定"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """パスワードの検証"""
        return check_password_hash(self.password_hash, password)   
        
    @property
    def is_authenticated(self):
        return True  # ログインしているユーザーは常にTrue

    @property
    def is_administrator(self):  # 管理者かどうかを確認するための別のプロパティ
        print(f"Checking administrator status: {self.administrator}")
        return self.administrator


# DynamoDBからデータを取得してUserインスタンスを作成する関数
def get_user_from_dynamodb(user_id):
    try:
        # DynamoDBからユーザーデータを取得
        response = app.dynamodb.get_item(
            TableName=app.table_name,
            Key={"user_id": {"S": user_id}}
        )
        
        # データが存在しない場合の処理
        if 'Item' not in response:
            print("User not found in DynamoDB.")
            return None

        item = response['Item']

        # DynamoDBのデータをUserクラスのインスタンスに変換
        user = User(
            user_id=item['user_id']['S'],
            display_name=item['display_name']['S'],
            user_name=item['user_name']['S'],
            furigana=item['furigana']['S'],
            email=item['email']['S'],
            password_hash=item['password']['S'],  # パスワードハッシュを設定
            gender=item['gender']['S'],
            date_of_birth=datetime.strptime(item['date_of_birth']['S'], '%Y-%m-%d').date(),
            post_code=item['post_code']['S'],
            address=item['address']['S'],
            phone=item['phone']['S'],
            organization=item.get('organization', {}).get('S', 'uguis'),
            administrator=bool(item.get('administrator', {}).get('BOOL', False))  # ブール型に変換
        )
        
        return user

    except Exception as e:
        print(f"Error fetching user from DynamoDB: {str(e)}")
        return None       

class LoginForm(FlaskForm):
    email = StringField(
        'メールアドレス',
        validators=[
            DataRequired(message='メールアドレスを入力してください'),
            Email(message='正しいメールアドレスの形式で入力してください')
        ]
    )
    password = PasswordField(
        'パスワード',
        validators=[
            DataRequired(message='パスワードを入力してください')
        ]
    )
    remember = BooleanField('ログイン状態を保持する')
    submit = SubmitField('ログイン')

    def __init__(self, *args, **kwargs):
        super(LoginForm, self).__init__(*args, **kwargs)
        self.user = None  # self.userを初期化

    def validate_email(self, field):
        """メールアドレスの存在確認"""
        try:
            # メールアドレスでユーザーを検索
            response = app.table.query(
                IndexName='email-index',
                KeyConditionExpression='email = :email',
                ExpressionAttributeValues={
                    ':email': field.data
                }
            )
            
            items = response.get('Items', [])
            if not items:
                raise ValidationError('このメールアドレスは登録されていません')
            
            # ユーザー情報を保存（パスワード検証で使用）
            self.user = items[0]
            
        except Exception as e:
            raise ValidationError('ログイン処理中にエラーが発生しました')

    def validate_password(self, field):
        """パスワードの検証"""
        if self.user is None:
            raise ValidationError('先にメールアドレスを確認してください')
            
        if not check_password_hash(self.user['password'], field.data):
            raise ValidationError('パスワードが正しくありません')

    def get_user(self):
        """ログイン成功時のユーザー情報を返す"""
        return self.user

@app.route("/")
@login_required
def index():       
        return render_template("index.html", posts=[])
    

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    form = RegistrationForm()
    if form.validate_on_submit():
        try:
            current_time = datetime.now().isoformat()
            hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
            user_id = str(uuid.uuid4())

            # メールアドレスの重複チェック用のクエリ
            email_check = app.dynamodb.query(
                TableName=app.table_name,
                IndexName='email-index',
                KeyConditionExpression='email = :email',
                ExpressionAttributeValues={
                    ':email': {'S': form.email.data}
                }
            )

            if email_check.get('Items'):
                app.logger.warning(f"Duplicate email registration attempt: {form.email.data}")
                flash('このメールアドレスは既に登録されています。', 'error')
                return redirect(url_for('signup'))

            # ユーザーの保存
            response = app.dynamodb.put_item(
                TableName=app.table_name,
                Item={
                    "user_id": {"S": user_id},
                    "organization": {"S": form.organization.data},  # 所属を追加
                    "address": {"S": form.address.data},
                    "administrator": {"BOOL": False},
                    "created_at": {"S": current_time},
                    "date_of_birth": {"S": form.date_of_birth.data.strftime('%Y-%m-%d')},
                    "display_name": {"S": form.display_name.data},
                    "email": {"S": form.email.data},
                    "furigana": {"S": form.furigana.data},
                    "gender": {"S": form.gender.data},
                    "password": {"S": hashed_password},
                    "phone": {"S": form.phone.data},
                    "post_code": {"S": form.post_code.data},
                    "updated_at": {"S": current_time},
                    "user_name": {"S": form.user_name.data}
                },
                ReturnValues="NONE"
            )

            # ログ出力を詳細に
            app.logger.info(f"New user created - ID: {user_id}, Organization: {form.organization.data}, Email: {form.email.data}")
            
            # 成功メッセージ
            flash('アカウントが作成されました！ログインしてください。', 'success')
            return redirect(url_for('login'))
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            app.logger.error(f"DynamoDB error - Code: {error_code}, Message: {error_message}")
            
            if error_code == 'ConditionalCheckFailedException':
                flash('このメールアドレスは既に登録されています。', 'error')
            elif error_code == 'ValidationException':
                flash('入力データが無効です。', 'error')
            elif error_code == 'ResourceNotFoundException':
                flash('システムエラーが発生しました。', 'error')
                app.logger.critical(f"DynamoDB table not found: {app.table_name}")
            else:
                flash('アカウント作成中にエラーが発生しました。', 'error')
                
            return redirect(url_for('signup'))
        
        except Exception as e:
            app.logger.error(f"Unexpected error during signup: {str(e)}", exc_info=True)
            flash('予期せぬエラーが発生しました。時間をおいて再度お試しください。', 'error')
            return redirect(url_for('signup'))
            
    # フォームのバリデーションエラーの場合
    if form.errors:
        app.logger.warning(f"Form validation errors: {form.errors}")
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{form[field].label.text}: {error}', 'error')
    
    return render_template('signup.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = LoginForm(dynamodb_table=app.table)
    if form.validate_on_submit():
        try:
            # メールアドレスでユーザーを取得
            response = app.table.query(
                IndexName='email-index',
                KeyConditionExpression='email = :email',
                ExpressionAttributeValues={
                    ':email': form.email.data.lower()
                }
            )
            
            items = response.get('Items', [])
            user_data = items[0] if items else None
            
            if not user_data:
                app.logger.warning(f"No user found for email: {form.email.data}")
                flash('メールアドレスまたはパスワードが正しくありません。', 'error')
                return render_template('login.html', form=form)           

            try:
                user = User(
                    user_id=user_data['user_id'],
                    display_name=user_data['display_name'],
                    user_name=user_data['user_name'],
                    furigana=user_data['furigana'],
                    email=user_data['email'],
                    password_hash=user_data['password'],
                    gender=user_data['gender'],
                    date_of_birth=user_data['date_of_birth'],
                    post_code=user_data['post_code'],
                    address=user_data['address'],
                    phone=user_data['phone']
                )
            except KeyError as e:
                app.logger.error(f"Error creating user object: {str(e)}")
                flash('ユーザーデータの読み込みに失敗しました。', 'error')
                return render_template('login.html', form=form)

            if not hasattr(user, 'check_password'):
                app.logger.error("User object missing check_password method")
                flash('ログイン処理中にエラーが発生しました。', 'error')
                return render_template('login.html', form=form)

            if user.check_password(form.password.data):
                login_user(user, remember=form.remember.data)
                app.logger.info(f"User logged in successfully - ID: {user.user_id}")
                flash('ログインに成功しました。', 'success')
                
                next_page = request.args.get('next')
                if not next_page or not is_safe_url(next_page):
                    next_page = url_for('index')
                return redirect(next_page)
            
            app.logger.warning(f"Invalid password attempt for email: {form.email.data}")
            time.sleep(random.uniform(0.1, 0.3))
            flash('メールアドレスまたはパスワードが正しくありません。', 'error')
                
        except Exception as e:
            app.logger.error(f"Login error: {str(e)}")
            flash('ログイン処理中にエラーが発生しました。', 'error')
    
    return render_template('login.html', form=form)

# セキュアなリダイレクト先かを確認する関数
def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

# セキュアなリダイレクト先かチェックする関数
def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and \
           ref_url.netloc == test_url.netloc

        
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")


@app.route("/user_maintenance", methods=["GET", "POST"])
@login_required
def user_maintenance():
    try:
        # テーブルからすべてのユーザーを取得
        response = app.table.scan()
        
        # デバッグ用に取得したユーザーデータを表示
        users = response.get('Items', [])
        app.logger.info(f"Retrieved {len(users)} users for maintenance page")

        return render_template(
            "user_maintenance.html",
            users=users,
            page=1,
            has_next=False
        )

    except ClientError as e:
        app.logger.error(f"DynamoDB error: {str(e)}")
        flash('ユーザー情報の取得に失敗しました。', 'error')
        return redirect(url_for('index'))


@app.route('/<string:user_id>/account', methods=['GET', 'POST'])  # UUIDは文字列なのでintからstringに変更
@login_required
def account(user_id):
    # DynamoDBからユーザー情報を取得
    try:
        response = app.dynamodb.get_item(
            TableName=app.table_name,
            Key={
                'user_id': {'S': user_id}
            }
        )
        user = response.get('Item')
        if not user:
            abort(404)
            
        # 現在のユーザーが対象ユーザーまたは管理者であることを確認
        if user['user_id']['S'] != current_user.get_id() and not current_user.is_administrator:
            abort(403)

        form = UpdateUserForm(user_id)
        
        if form.validate_on_submit():
            current_time = datetime.now().isoformat()
            
            # パスワードが入力された場合はハッシュ化
            update_expression_parts = []
            expression_values = {}
            
            # 更新する項目を設定
            if form.user_name.data:
                update_expression_parts.append("user_name = :user_name")
                expression_values[':user_name'] = {'S': form.user_name.data}
                
            if form.email.data:
                update_expression_parts.append("email = :email")
                expression_values[':email'] = {'S': form.email.data}
                
            if form.password.data:
                hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
                update_expression_parts.append("password = :password")
                expression_values[':password'] = {'S': hashed_password}

            # 更新日時は常に更新
            update_expression_parts.append("updated_at = :updated_at")
            expression_values[':updated_at'] = {'S': current_time}

            # DynamoDBを更新
            response = app.dynamodb.update_item(
                TableName=app.table_name,
                Key={
                    'user_id': {'S': user_id}
                },
                UpdateExpression="SET " + ", ".join(update_expression_parts),
                ExpressionAttributeValues=expression_values,
                ReturnValues="UPDATED_NEW"
            )
            
            flash('ユーザーアカウントが更新されました', 'success')
            return redirect(url_for('user_maintenance'))
            
        elif request.method == 'GET':
            # フォームに現在の値を設定
            form.user_name.data = user.get('user_name', {}).get('S', '')
            form.email.data = user.get('email', {}).get('S', '')
            
        return render_template('account.html', form=form)
        
    except ClientError as e:
        app.logger.error(f"DynamoDB error: {str(e)}")
        flash('データベースエラーが発生しました。', 'error')
        return redirect(url_for('user_maintenance'))


    
@app.route("/<string:post_id>/update", methods=["GET", "POST"])
@login_required
def update(post_id):
    try:
        # GETリクエスト: 投稿データを取得して編集フォームを表示
        if request.method == "GET":
            # DynamoDBから投稿を取得
            response = app.dynamodb.get_item(
                TableName=app.posts_table_name,  # 投稿用のテーブル名
                Key={
                    'post_id': {'S': post_id}
                }
            )
            
            post = response.get('Item')
            if not post:
                flash('投稿が見つかりません', 'error')
                return redirect(url_for('index'))
                
            # 投稿者かadminのみ編集可能
            if post.get('user_id', {}).get('S') != current_user.get_id() and not current_user.is_administrator:
                flash('編集権限がありません', 'error')
                return redirect(url_for('index'))
                
            return render_template("update.html", post=post)
    
        # POSTリクエスト: 投稿を更新
        else:
            current_time = datetime.now().isoformat()
            
            # 更新する項目を設定
            update_expression_parts = []
            expression_values = {}
            
            # タイトルの更新
            if request.form.get("title"):
                update_expression_parts.append("title = :title")
                expression_values[':title'] = {'S': request.form.get("title")}
                
            # 本文の更新
            if request.form.get("body"):
                update_expression_parts.append("body = :body")
                expression_values[':body'] = {'S': request.form.get("body")}
                
            # カテゴリーの更新
            if request.form.get("category_id"):
                update_expression_parts.append("category_id = :category_id")
                expression_values[':category_id'] = {'S': request.form.get("category_id")}
            
            # 更新日時の設定
            update_expression_parts.append("updated_at = :updated_at")
            expression_values[':updated_at'] = {'S': current_time}
            
            # DynamoDBの更新
            response = app.dynamodb.update_item(
                TableName=app.posts_table_name,
                Key={
                    'post_id': {'S': post_id}
                },
                UpdateExpression="SET " + ", ".join(update_expression_parts),
                ExpressionAttributeValues=expression_values,
                ReturnValues="UPDATED_NEW"
            )
            
            flash('投稿が更新されました', 'success')
            return redirect(url_for('index'))
            
    except ClientError as e:
        app.logger.error(f"DynamoDB error: {str(e)}")
        flash('データベースエラーが発生しました', 'error')
        return redirect(url_for('index'))
                            

@app.route("/<int:id>/delete")
@login_required
def delete(id):
    post = Post.query.get(id)
    db.session.delete(post)
    db.session.commit()
    return redirect("/")  


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)