import sqlite3

# 连接到数据库
conn = sqlite3.connect('instance/game_scoring.db')
cursor = conn.cursor()

try:
    # 添加ip_address字段
    cursor.execute("ALTER TABLE user_session ADD COLUMN ip_address VARCHAR(50)")
    print("成功添加ip_address字段")
    
    # 添加region字段
    cursor.execute("ALTER TABLE user_session ADD COLUMN region VARCHAR(100)")
    print("成功添加region字段")
    
    # 提交更改
    conn.commit()
    print("数据库表结构更新成功！")
    
except sqlite3.OperationalError as e:
    print(f"可能字段已存在或其他错误: {e}")
    # 尝试继续执行，忽略已存在的字段错误
finally:
    # 关闭连接
    conn.close()