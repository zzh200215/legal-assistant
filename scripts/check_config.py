#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置诊断工具
用于检查应用配置的完整性和正确性
"""
import sys
import os
from pathlib import Path

# 设置UTF-8输出编码（Windows兼容）
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def print_section(title: str):
    """打印分节标题"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def check_env_file():
    """检查.env文件是否存在"""
    print_section("环境文件检查")

    env_path = Path(".env")
    env_example_path = Path(".env.example")

    if env_path.exists():
        print(f"✓ .env 文件存在: {env_path.absolute()}")
        print(f"  文件大小: {env_path.stat().st_size} 字节")
    else:
        print("✗ .env 文件不存在")
        if env_example_path.exists():
            print(f"  提示: 可以复制 .env.example 创建 .env 文件")
            print(f"        cp .env.example .env")
        return False

    if env_example_path.exists():
        print(f"✓ .env.example 文件存在")

    return True


def check_config_loading():
    """检查配置加载"""
    print_section("配置加载检查")

    try:
        from app.core.config import get_settings, check_config_health

        print("尝试加载配置...")
        settings = get_settings()
        print("✓ 配置加载成功")

        # 显示关键配置状态
        print("\n关键配置状态:")
        print(f"  数据库: {settings.DATABASE_URL[:50]}...")
        print(f"  Redis: {settings.REDIS_URL}")
        print(f"  LLM提供商: {settings.LLM_PROVIDER}")
        print(f"  LLM模型: {settings.LLM_MODEL}")
        print(f"  向量存储: {settings.VECTOR_STORE_PROVIDER}")

        # API密钥状态（不显示实际值）
        api_key_status = "已配置" if settings.LLM_API_KEY else "未配置"
        print(f"  LLM API密钥: {api_key_status}")

        secret_key_status = "已配置" if settings.SECRET_KEY else "未配置"
        print(f"  SECRET_KEY: {secret_key_status}")

        return True

    except ValueError as e:
        print(f"✗ 配置验证失败:")
        print(f"  {str(e)}")
        return False
    except Exception as e:
        print(f"✗ 配置加载错误:")
        print(f"  {type(e).__name__}: {str(e)}")
        return False


def check_config_health_status():
    """检查配置健康状态"""
    print_section("配置健康检查")

    try:
        from app.core.config import check_config_health

        health = check_config_health()

        status_emoji = {
            "healthy": "✓",
            "warning": "⚠",
            "unhealthy": "✗",
            "error": "✗"
        }

        emoji = status_emoji.get(health["status"], "?")
        print(f"{emoji} 整体状态: {health['status'].upper()}")

        if health.get("env_file"):
            print(f"  使用配置文件: {health['env_file']}")

        if health.get("issues"):
            print("\n问题:")
            for issue in health["issues"]:
                print(f"  ✗ {issue}")

        if health.get("warnings"):
            print("\n警告:")
            for warning in health["warnings"]:
                print(f"  ⚠ {warning}")

        if health["status"] == "healthy":
            print("\n✓ 所有配置检查通过")
            return True
        elif health["status"] == "warning":
            print("\n⚠ 配置可用但存在警告，生产环境请修复")
            return True
        else:
            return False

    except Exception as e:
        print(f"✗ 健康检查失败:")
        print(f"  {type(e).__name__}: {str(e)}")
        return False


def generate_secret_keys():
    """生成密钥示例"""
    print_section("密钥生成工具")

    try:
        import secrets

        print("生成强随机密钥示例:")
        print("\n1. SECRET_KEY (用于JWT签名等):")
        secret_key = secrets.token_urlsafe(32)
        print(f"   {secret_key}")

        print("\n2. CONNECTOR_CREDENTIAL_ENCRYPTION_KEY (Fernet密钥):")
        try:
            from cryptography.fernet import Fernet
            fernet_key = Fernet.generate_key().decode()
            print(f"   {fernet_key}")
        except ImportError:
            print("   需要安装 cryptography 库: pip install cryptography")

        print("\n3. LEGAL_DATA_ENCRYPTION_KEY (32字节Base64密钥):")
        legal_key = secrets.token_urlsafe(32)
        print(f"   {legal_key}")

        print("\n提示: 将以上密钥复制到.env文件对应位置")

    except Exception as e:
        print(f"✗ 密钥生成失败: {e}")


def main():
    """主函数"""
    print("=" * 60)
    print("  AI办公助手 - 配置诊断工具")
    print("=" * 60)

    results = []

    # 检查环境文件
    results.append(check_env_file())

    # 检查配置加载
    if results[-1]:
        results.append(check_config_loading())
        results.append(check_config_health_status())

    # 生成密钥示例
    generate_secret_keys()

    # 总结
    print_section("检查总结")

    if all(results):
        print("✓ 所有检查通过，配置正常")
        print("\n可以启动应用:")
        print("  python -m uvicorn app.main:app --reload")
        return 0
    else:
        print("✗ 发现配置问题，请根据上述提示修复")
        print("\n常见问题解决:")
        print("  1. 如果.env不存在: cp .env.example .env")
        print("  2. 配置API密钥: 编辑.env，设置LLM_API_KEY")
        print("  3. 配置安全密钥: 使用上方生成的密钥替换默认值")
        return 1


if __name__ == "__main__":
    sys.exit(main())
