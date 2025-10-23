from app import app, db

# 在应用上下文中运行数据库迁移
with app.app_context():
    print("开始数据库迁移...")
    db.create_all()
    print("数据库迁移完成！")