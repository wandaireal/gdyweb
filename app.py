from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from datetime import datetime, timedelta
import os
import json
import logging
import socket
import platform
import sys
import requests

# 导入dotenv模块，用于从.env文件加载环境变量
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志系统
logs_dir = 'logs'
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# 创建日志文件路径
log_filename = os.path.join(logs_dir, f'user_log_{datetime.now().strftime("%Y-%m-%d")}.log')

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('game_scoring_app')

# 记录系统信息
def log_system_info():
    logger.info(f"系统信息: {platform.system()} {platform.release()}")
    logger.info(f"Python版本: {sys.version}")
    logger.info(f"主机名: {socket.gethostname()}")
    
app = Flask(__name__)
# 使用环境变量中的SECRET_KEY，如果没有则使用默认值
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# 配置数据库URI
# 优先使用Render.com提供的DATABASE_URL环境变量（PostgreSQL）
# 如果没有，则使用SQLite作为后备
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///game_scoring.db')

# 如果DATABASE_URL存在，确保它使用正确的PostgreSQL驱动
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# 数据库模型
class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    login_time = db.Column(db.DateTime, default=datetime.utcnow)
    logout_time = db.Column(db.DateTime)
    duration = db.Column(db.Integer)
    user_agent = db.Column(db.Text)
    ip_address = db.Column(db.String(50), nullable=True)
    region = db.Column(db.String(100), nullable=True)

class GameRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_session_id = db.Column(db.Integer, db.ForeignKey('user_session.id'), nullable=False)
    game_start_time = db.Column(db.DateTime, default=datetime.utcnow)
    game_end_time = db.Column(db.DateTime)
    player_count = db.Column(db.Integer)
    player_names = db.Column(db.Text)
    round_scores = db.Column(db.Text)
    total_scores = db.Column(db.Text)
    # 添加与UserSession的关系
    user_session = db.relationship('UserSession', backref='game_records')

# 辅助函数：获取真实IP地址
def get_real_ip(request):
    """获取用户真实IP地址，支持代理和负载均衡环境，特别是Render平台"""
    # 尝试从各种代理头获取真实IP
    headers = request.headers
    
    # 首先检查Render平台常用的代理头
    real_ip = headers.get('X-Forwarded-For', '').split(',')[0].strip()
    if not real_ip:
        real_ip = headers.get('X-Real-IP', '').strip()
    if not real_ip:
        real_ip = headers.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
    if not real_ip:
        real_ip = headers.get('HTTP_X_REAL_IP', '').strip()
    if not real_ip:
        forwarded = headers.get('Forwarded', '').split(',')[0].strip()
        if forwarded.startswith('for='):
            real_ip = forwarded.split('=')[1].strip()
    if not real_ip:
        real_ip = request.remote_addr
    
    # 移除可能的端口号
    if ':' in real_ip:
        real_ip = real_ip.split(':')[0]
    
    return real_ip or 'Unknown'

# 辅助函数：获取IP地址对应的地理位置信息
def get_ip_location(ip):
    """使用ipinfo.io API获取IP地址的地理位置信息，增强了在云平台上的兼容性"""
    # 记录IP地址信息用于调试
    logger.info(f"处理IP地址位置查询 - IP: {ip}")
    
    # 检查是否为本地或内网IP
    if ip == '127.0.0.1' or ip == 'localhost' or ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.16.') or ip.startswith('172.31.'):
        logger.info(f"IP {ip} 被识别为本地/内网IP")
        return '本地网络'
    
    # 检查是否为负载均衡器或云平台IP
    load_balancer_ips = ['10.10.10.1', '10.0.0.1']  # 可根据Render平台实际情况添加
    if ip in load_balancer_ips:
        logger.info(f"IP {ip} 被识别为负载均衡器IP")
        return '云平台负载均衡器'
    
    try:
        # 尝试多次请求以提高成功率
        for attempt in range(2):
            try:
                # 使用ipinfo.io的免费API
                url = f'https://ipinfo.io/{ip}/json'
                # 添加超时和重试配置
                response = requests.get(url, timeout=5, headers={'Accept': 'application/json'})
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        # 记录返回的数据用于调试
                        logger.info(f"IP {ip} 地理位置信息: {data}")
                        
                        # 构建地区信息
                        city = data.get('city', '')
                        region = data.get('region', '')
                        country = data.get('country', '')
                        
                        location_parts = []
                        if country:
                            location_parts.append(country)
                        if region and region != city:
                            location_parts.append(region)
                        if city:
                            location_parts.append(city)
                        
                        location = ', '.join(location_parts)
                        return location or '未知地区'
                    except json.JSONDecodeError:
                        logger.error(f"解析IP位置JSON失败 - IP: {ip}")
                elif response.status_code == 429:
                    logger.warning(f"IP查询达到API限制 - IP: {ip}, 状态码: {response.status_code}")
                    # 如果是API限制，使用备用方案
                    return f'IP: {ip}'
                else:
                    logger.warning(f"IP查询失败 - IP: {ip}, 状态码: {response.status_code}")
            except requests.RequestException as e:
                logger.warning(f"IP查询请求异常(尝试 {attempt+1}) - IP: {ip}, 错误: {str(e)}")
                if attempt == 0:
                    import time
                    time.sleep(1)  # 短暂等待后重试
    except Exception as e:
        logger.error(f"获取IP位置信息失败 - IP: {ip}, 错误: {str(e)}")
    
    # 如果所有尝试都失败，至少返回IP地址
    return f'IP: {ip}'

# 创建数据库表
with app.app_context():
    db.create_all()
    log_system_info()

# 注册中文字体
def register_chinese_fonts():
    """注册中文字体"""
    try:
        # 尝试注册常见的中文字体
        font_paths = [
            'fonts/SourceHanSansCN-Regular.ttf',  # 思源黑体
            'fonts/simhei.ttf',  # 黑体
            'fonts/simsun.ttc',  # 宋体
            'fonts/msyh.ttf',    # 微软雅黑
            'fonts/wqy-microhei.ttc',  # 文泉驿微米黑
            'C:/Windows/Fonts/simhei.ttf',  # Windows 系统字体
            'C:/Windows/Fonts/msyh.ttf',    # Windows 微软雅黑
            '/System/Library/Fonts/PingFang.ttc',  # macOS 苹方
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'  # Linux 文泉驿
        ]
        
        chinese_font = None
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font_name = os.path.splitext(os.path.basename(font_path))[0]
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    chinese_font = font_name
                    print(f"成功注册字体: {font_name}")
                    break
                except Exception as e:
                    print(f"注册字体失败 {font_path}: {e}")
                    continue
        
        if chinese_font is None:
            # 如果没有找到字体文件，尝试使用默认字体
            print("警告：未找到中文字体文件，中文可能显示为方块")
            return 'Helvetica'
        
        return chinese_font
    except Exception as e:
        print(f"字体注册出错: {e}")
        return 'Helvetica'

# 在应用启动时注册字体
CHINESE_FONT = register_chinese_fonts()

# 添加时区转换过滤器
@app.template_filter('to_utc8')
def to_utc8(dt):
    """将UTC时间转换为东八区（北京时间）"""
    if dt:
        # UTC+8
        return dt + timedelta(hours=8)
    return None

# PDF生成功能（支持中文）
def generate_score_pdf(player_names, round_history, total_scores, filename):
    """生成得分PDF报告（支持中文）"""
    try:
        doc = SimpleDocTemplate(filename, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        # 创建支持中文的样式
        chinese_style = styles['Normal']
        chinese_style.fontName = CHINESE_FONT
        chinese_style.fontSize = 12
        chinese_style.leading = 14
        
        title_style = styles['Title']
        title_style.fontName = CHINESE_FONT
        title_style.fontSize = 16
        title_style.leading = 18
        
        heading_style = styles['Heading2']
        heading_style.fontName = CHINESE_FONT
        heading_style.fontSize = 14
        heading_style.leading = 16
        
        # 标题
        title = Paragraph("游戏得分记录", title_style)
        story.append(title)
        
        # 添加游戏信息
        info_text = f"玩家数量: {len(player_names)} | 游戏回合: {len(round_history)}"
        info_para = Paragraph(info_text, chinese_style)
        story.append(info_para)
        
        story.append(Paragraph("<br/><br/>", chinese_style))  # 空行
        
        # 准备表格数据
        table_data = [['回合'] + player_names]
        
        # 添加每轮得分
        for i, round_scores in enumerate(round_history, 1):
            row = [f'第{i}轮']
            
            # 找出本轮获胜玩家（得分最高的玩家）
            round_winner = max(round_scores.items(), key=lambda x: x[1])[0]
            
            for player in player_names:
                score = round_scores.get(player, 0)
                # 标记获胜玩家（得分最高的玩家）
                if player == round_winner:
                    row.append(f'{score:.2f} (胜)')
                else:
                    row.append(f'{score:.2f}')
            table_data.append(row)
        
        # 添加总分行
        total_row = ['总分']
        for player in player_names:
            total_row.append(f'{total_scores.get(player, 0):.2f}')
        table_data.append(total_row)
        
        # 创建表格
        table = Table(table_data)
        
        # 设置表格样式
        table_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), CHINESE_FONT),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightblue),
            ('FONTNAME', (0, -1), (-1, -1), CHINESE_FONT),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ])
        
        # 为获胜玩家添加特殊样式
        for i, round_scores in enumerate(round_history, 1):
            round_winner = max(round_scores.items(), key=lambda x: x[1])[0]
            winner_col = player_names.index(round_winner) + 1  # +1 因为第一列是回合号
            
            # 为获胜玩家的得分添加背景色
            table_style.add('BACKGROUND', (winner_col, i), (winner_col, i), colors.lightgreen)
        
        table.setStyle(table_style)
        story.append(table)
        
        # 添加最终排名
        story.append(Paragraph("<br/><br/>", chinese_style))  # 空行
        
        # 计算排名
        sorted_players = sorted(total_scores.items(), key=lambda x: x[1], reverse=True)
        
        # 添加最终排名标题
        ranking_title = Paragraph("最终排名", heading_style)
        story.append(ranking_title)
        
        # 添加排名列表
        for i, (player, score) in enumerate(sorted_players, 1):
            rank_text = f"{i}. {player}: {score:.2f} 分"
            if i == 1:
                rank_text += "W"  # 为冠军添加奖杯符号
            rank_para = Paragraph(rank_text, chinese_style)
            story.append(rank_para)
        
        # 添加游戏规则说明
        # story.append(Paragraph("<br/><br/>", chinese_style))  # 空行
        # rules_title = Paragraph("游戏规则说明", heading_style)
        # story.append(rules_title)
        
        # rules_text = """
        # 1. 每轮选择一名获胜玩家，其他玩家输入负分<br/>
        # 2. 获胜玩家获得其他玩家负分总和的绝对值作为得分<br/>
        # 3. 所有玩家得分总和为0<br/>
        # 4. 最终排名按总分从高到低排列
        # """
        # rules_para = Paragraph(rules_text, chinese_style)
        # story.append(rules_para)
        
        # 添加生成时间
        story.append(Paragraph("<br/><br/>", chinese_style))  # 空行
        gen_time = Paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", chinese_style)
        story.append(gen_time)
        
        doc.build(story)
        return True
        
    except Exception as e:
        print(f"生成PDF时出错: {e}")
        return False

@app.route('/')
def index():
    # 直接重定向到设置页面
    return redirect(url_for('setup_game'))
    
@app.route('/login', methods=['GET', 'POST'])
def login():
    # 保留登录功能，但不作为默认路由
    if request.method == 'POST':
        try:
            # 获取用户输入
            username = request.form['username']
            email = request.form['email']
            user_agent = request.headers.get('User-Agent', 'Unknown')
            
            # 使用新函数获取真实IP地址
            ip_address = get_real_ip(request)
            # 获取IP对应的地理位置
            region = get_ip_location(ip_address)
            
            # 记录登录尝试
            logger.info(f"登录尝试 - 用户名: {username}, 邮箱: {email}, IP: {ip_address}, 地区: {region}, 用户代理: {user_agent}")
            
            # 创建用户会话记录
            user_session = UserSession(
                username=username,
                email=email,
                user_agent=user_agent,
                ip_address=ip_address,
                region=region
            )
            db.session.add(user_session)
            db.session.commit()
            
            # 存储用户信息到session
            session['user_session_id'] = user_session.id
            session['username'] = username
            session['email'] = email
            
            # 记录登录成功
            logger.info(f"登录成功 - 用户会话ID: {user_session.id}, 用户名: {username}")
            
            # 重定向到游戏设置页面
            return redirect(url_for('setup_game'))
        except Exception as e:
            # 记录登录失败
            logger.error(f"登录失败 - 错误: {str(e)}")
            flash(f"登录失败: {str(e)}")
            return redirect(url_for('login'))
    
    # GET请求时显示登录页面
    return render_template('index.html')

@app.route('/setup', methods=['GET', 'POST'])
def setup_game():
    # 如果用户未登录，创建一个匿名会话
    if 'user_session_id' not in session:
        username = '匿名用户'
        email = 'anonymous@example.com'
        user_agent = request.headers.get('User-Agent', 'Unknown')
        
        # 使用新函数获取真实IP地址
        ip_address = get_real_ip(request)
        # 获取IP对应的地理位置
        region = get_ip_location(ip_address)
        
        # 创建用户会话记录
        user_session = UserSession(
            username=username,
            email=email,
            user_agent=user_agent,
            ip_address=ip_address,
            region=region
        )
        db.session.add(user_session)
        db.session.commit()
        
        # 存储用户信息到session
        session['user_session_id'] = user_session.id
        session['username'] = username
        session['email'] = email
        
        logger.info(f"创建匿名会话 - 会话ID: {user_session.id}")
    
    username = session.get('username', 'Unknown')
    logger.info(f"访问游戏设置页面 - 会话ID: {session['user_session_id']}, 用户名: {username}")
    
    if request.method == 'POST':
        try:
            # 获取玩家数量
            player_count = int(request.form['player_count'])
            
            # 获取玩家名称
            player_names = []
            for i in range(1, player_count + 1):
                field_name = f"player{i}"
                player_name = request.form.get(field_name, '').strip()
                if player_name:
                    player_names.append(player_name)
            
            # 验证玩家数量
            if len(player_names) != player_count:
                return f"错误：请为所有玩家输入名称。预期{player_count}个，实际输入{len(player_names)}个。"
            
            # 存储游戏设置到session
            session['player_count'] = player_count
            session['player_names'] = player_names
            session['current_round'] = 1
            session['scores'] = {name: 0 for name in player_names}
            session['round_history'] = []  # 初始化回合历史
            
            # 记录游戏设置完成
            logger.info(f"游戏设置完成 - 会话ID: {session['user_session_id']}, 用户名: {username}, 玩家: {', '.join(player_names)}")
            
            # 创建游戏记录并设置开始时间
            game_record = GameRecord(
                user_session_id=session['user_session_id'],
                game_start_time=datetime.utcnow(),
                player_count=session['player_count'],
                player_names=json.dumps(session['player_names'])
            )
            db.session.add(game_record)
            db.session.commit()
            
            # 存储游戏记录ID到session
            session['game_record_id'] = game_record.id
            
            # 重定向到计分页面
            return redirect(url_for('scoring'))
        
        except Exception as e:
            return f"处理表单时出错: {str(e)}"
    
    # GET请求时显示游戏设置页面
    return render_template('setup.html')

@app.route('/scoring', methods=['GET', 'POST'])
def scoring():
    # 检查游戏设置是否存在
    if 'player_names' not in session:
        logger.warning(f"未授权访问计分页面 - IP: {request.remote_addr}")
        return redirect(url_for('setup_game'))
    
    username = session.get('username', 'Unknown')
    
    if request.method == 'POST':
        try:
            # 获取获胜玩家
            winner = request.form.get('winner')
            if not winner:
                return "错误：请选择一名获胜玩家。"
            
            # 获取本轮得分
            round_scores = {}
            total_negative = 0
            
            # 获取所有玩家的得分
            for player in session['player_names']:
                score_key = f"score_{player}"
                score_value = request.form.get(score_key, "").strip()
                
                if score_value:  # 如果用户输入了分数
                    score = float(score_value)
                    # 验证分数是否为负（获胜玩家除外）
                    if player != winner and score >= 0:
                        return f"错误：玩家 {player} 的得分必须为负分。"
                    
                    round_scores[player] = score
                    if player != winner:
                        total_negative += score
                else:
                    # 如果没有输入分数，且不是获胜玩家，返回错误
                    if player != winner:
                        return f"错误：请为玩家 {player} 输入负分。"
            
            # 计算获胜玩家的得分（使总和为0）
            round_scores[winner] = -total_negative
            
            # 更新总分
            for player, score in round_scores.items():
                session['scores'][player] += score
            
            # 记录本轮得分
            session['round_history'].append(round_scores)
            
            # 记录回合完成
            logger.info(f"回合完成 - 会话ID: {session['user_session_id']}, 用户名: {username}, 回合: {session['current_round']}, 获胜者: {winner}")
            
            # 增加回合数
            session['current_round'] += 1
            
            # 重定向回计分页面（显示更新后的分数）
            return redirect(url_for('scoring'))
        
        except Exception as e:
            return f"处理得分时出错: {str(e)}"
    
    # GET请求时显示计分页面
    return render_template('scoring.html')

@app.route('/end_game')
def end_game():
    # 检查游戏设置是否存在
    if 'player_names' not in session:
        logger.warning(f"未授权访问游戏结束页面 - IP: {request.remote_addr}")
        return redirect(url_for('setup_game'))
    
    try:
        username = session.get('username', 'Unknown')
        
        # 记录游戏结束
        logger.info(f"游戏结束 - 用户会话ID: {session['user_session_id']}, 用户名: {username}, 玩家数量: {session['player_count']}")
        
        # 更新游戏记录
        if 'game_record_id' in session:
            game_record = GameRecord.query.get(session['game_record_id'])
            if game_record:
                game_record.game_end_time = datetime.utcnow()
                game_record.round_scores = json.dumps(session['round_history'])
                game_record.total_scores = json.dumps(session['scores'])
        else:
            # 如果没有找到游戏记录，创建新的
            game_record = GameRecord(
                user_session_id=session['user_session_id'],
                game_start_time=datetime.utcnow(),  # 设置游戏开始时间
                game_end_time=datetime.utcnow(),
                player_count=session['player_count'],
                player_names=json.dumps(session['player_names']),
                round_scores=json.dumps(session['round_history']),
                total_scores=json.dumps(session['scores'])
            )
            db.session.add(game_record)
        
        # 更新用户会话的退出时间和使用时长
        user_session = UserSession.query.get(session['user_session_id'])
        if user_session:
            user_session.logout_time = datetime.utcnow()
            user_session.duration = int((user_session.logout_time - user_session.login_time).total_seconds())
            logger.info(f"用户登出 - 会话ID: {user_session.id}, 用户名: {username}, 持续时间: {user_session.duration}秒")
        
        db.session.commit()
        
        # 生成得分PDF报告
        filename = f"score_report_{session['user_session_id']}.pdf"
        filepath = os.path.join('static', filename)
        
        # 确保static目录存在
        if not os.path.exists('static'):
            os.makedirs('static')
        
        # 生成PDF
        success = generate_score_pdf(
            session['player_names'],
            session['round_history'],
            session['scores'],
            filepath
        )
        
        if success:
            logger.info(f"PDF生成成功 - 会话ID: {session['user_session_id']}, 文件名: {filename}")
            return render_template('end_game.html', pdf_filename=filename)
        else:
            logger.error(f"PDF生成失败 - 会话ID: {session['user_session_id']}")
            return render_template('end_game.html', 
                                 error_message="PDF生成失败，但游戏记录已保存",
                                 pdf_filename=None)
    
    except Exception as e:
        logger.error(f"处理游戏结束时出错 - 会话ID: {session.get('user_session_id')}, 错误: {str(e)}")
        return f"处理游戏结束时出错: {str(e)}"

# PDF下载路由
@app.route('/download_pdf/<filename>')
def download_pdf(filename):
    """下载PDF报告"""
    try:
        username = session.get('username', 'Unknown')
        user_session_id = session.get('user_session_id', 'Unknown')
        
        # 记录PDF下载
        logger.info(f"PDF下载 - 会话ID: {user_session_id}, 用户名: {username}, 文件名: {filename}")
        
        return send_file(os.path.join('static', filename), as_attachment=True)
    except Exception as e:
        logger.error(f"PDF下载失败 - 文件名: {filename}, 错误: {str(e)}")
        return "文件不存在或已删除"

# 调试页面
@app.route('/debug')
def debug():
    # 使用新函数获取真实IP地址
    visitor_ip = get_real_ip(request)
    
    # 记录调试页面访问
    username = session.get('username', 'Unknown')
    user_session_id = session.get('user_session_id', 'Unknown')
    logger.info(f"访问调试页面 - 会话ID: {user_session_id}, 用户名: {username}, IP: {visitor_ip}")
    
    return f"""
    <h1>调试信息</h1>
    <p>用户会话ID: {session.get('user_session_id', '未设置')}</p>
    <p>用户名: {session.get('username', '未设置')}</p>
    <p>邮箱: {session.get('email', '未设置')}</p>
    <p>玩家数量: {session.get('player_count', '未设置')}</p>
    <p>玩家名称: {session.get('player_names', '未设置')}</p>
    <p>当前回合: {session.get('current_round', '未设置')}</p>
    <p>得分: {session.get('scores', '未设置')}</p>
    <p>回合历史: {session.get('round_history', '未设置')}</p>
    <p>当前使用字体: {CHINESE_FONT}</p>
    <p>日志文件: {log_filename}</p>
    <p><a href="/setup">返回设置页面</a></p>
    """

# 添加手动登出路由
@app.route('/logout')
def logout():
    """手动登出功能"""
    # 使用新函数获取真实IP地址
    visitor_ip = get_real_ip(request)
    
    username = session.get('username', 'Unknown')
    user_session_id = session.get('user_session_id', None)
    
    if user_session_id:
        # 更新用户会话的退出时间和使用时长
        try:
            user_session = UserSession.query.get(user_session_id)
            if user_session:
                user_session.logout_time = datetime.utcnow()
                user_session.duration = int((user_session.logout_time - user_session.login_time).total_seconds())
                db.session.commit()
                logger.info(f"手动登出 - 会话ID: {user_session_id}, 用户名: {username}, IP: {visitor_ip}, 持续时间: {user_session.duration}秒")
        except Exception as e:
            logger.error(f"更新登出信息失败 - 会话ID: {user_session_id}, 错误: {str(e)}")
    else:
        logger.warning(f"尝试登出但无活动会话 - IP: {visitor_ip}")
    
    # 清除session
    session.clear()
    
    return redirect(url_for('index'))

# 管理员登录路由
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    # 如果已经登录，直接跳转到统计页面
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_stats'))
    
    # 使用新函数获取真实IP地址和地理位置
    admin_ip = get_real_ip(request)
    admin_region = get_ip_location(admin_ip)
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # 验证用户名和密码
        if username == 'admin' and password == '123123':
            # 登录成功，设置会话
            session['admin_logged_in'] = True
            logger.info(f"管理员登录成功 - IP: {admin_ip}, 地区: {admin_region}")
            return redirect(url_for('admin_stats'))
        else:
            # 登录失败
            logger.warning(f"管理员登录失败 - 用户名: {username}, IP: {admin_ip}, 地区: {admin_region}")
            return render_template('admin_login.html', error='用户名或密码错误')
    
    # GET请求显示登录页面
    return render_template('admin_login.html')

# 管理员统计页面路由
@app.route('/admin/stats')
def admin_stats():
    # 使用新函数获取真实IP地址
    visitor_ip = get_real_ip(request)
    
    # 检查是否已登录
    if not session.get('admin_logged_in'):
        logger.warning(f"未授权访问管理员统计页面 - IP: {visitor_ip}")
        return redirect(url_for('admin_login'))
    
    try:
        # 获取所有游戏记录，预加载user_session关系
        game_records = GameRecord.query.options(db.joinedload(GameRecord.user_session)).all()
        
        # 计算总游戏次数
        total_games = len(game_records)
        
        # 计算总游戏轮数
        total_rounds = 0
        for record in game_records:
            try:
                round_history = json.loads(record.round_scores)
                total_rounds += len(round_history)
                # 添加回合数属性到记录对象
                record.round_count = len(round_history)
            except:
                record.round_count = 0
        
        # 计算每个游戏的最高得分者和分数
        for record in game_records:
            try:
                total_scores = json.loads(record.total_scores)
                if total_scores:
                    top_scorer = max(total_scores.items(), key=lambda x: x[1])
                    record.top_scorer = top_scorer[0]
                    record.top_score = top_scorer[1]
                else:
                    record.top_scorer = '未知'
                    record.top_score = 0
            except:
                record.top_scorer = '未知'
                record.top_score = 0
        
        logger.info(f"管理员查看统计页面 - 总游戏数: {total_games}, 总轮数: {total_rounds}")
        
        return render_template('admin_stats.html', 
                             total_games=total_games,
                             total_rounds=total_rounds,
                             game_records=game_records)
    
    except Exception as e:
        logger.error(f"获取统计数据失败: {str(e)}")
        return f"获取统计数据时出错: {str(e)}"

# 管理员登出路由
@app.route('/admin/logout')
def admin_logout():
    # 使用新函数获取真实IP地址
    visitor_ip = get_real_ip(request)
    
    # 清除管理员登录状态
    session.pop('admin_logged_in', None)
    logger.info(f"管理员登出 - IP: {visitor_ip}")
    return redirect(url_for('admin_login'))

# 在文件末尾修改启动代码
if __name__ == '__main__':
    logger.info("应用程序启动")
    # 在生产环境中，使用环境变量PORT或默认端口
    port = int(os.environ.get('PORT', 5002))
    # 生产环境中关闭debug模式
    debug_mode = os.environ.get('DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, port=port, host='0.0.0.0')

# WSGI入口点，Render.com会寻找这个
wsgi_app = app