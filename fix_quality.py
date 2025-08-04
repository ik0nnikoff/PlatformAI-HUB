#!/usr/bin/env python3
"""
Скрипт для массового исправления проблем качества кода в Python файлах.
"""

import re
import sys

def fix_no_else_return(content):
    """
    Исправляет проблемы с no-else-return.
    """
    # Паттерн для поиска if-else-return конструкций
    pattern = r'(\s+)(if\s+[^:]+:[\s\S]*?return\s+[^\n]+)\n(\s+)else:\n(\s+)(.*?)return\s+([^\n]+)'
    
    def replace_else_return(match):
        indent = match.group(1)
        if_block = match.group(2)
        else_indent = match.group(3)
        else_content_indent = match.group(4)
        else_content = match.group(5)
        return_value = match.group(6)
        
        # Убираем else и делаем код на том же уровне
        return f"{if_block}\n{else_indent}\n{else_indent}{else_content}return {return_value}"
    
    return re.sub(pattern, replace_else_return, content, flags=re.MULTILINE)

def fix_missing_docstrings(content):
    """
    Добавляет базовые docstrings для методов без них.
    """
    # Находим методы без docstrings
    method_pattern = r'(\s+)(async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\).*?:\n(\s+)(?!"""|\'\'\')(.*?)(?=\n\s+def|\n\s+async\s+def|\nclass|\n[a-zA-Z]|\Z)'
    
    def add_docstring(match):
        indent = match.group(1)
        async_keyword = match.group(2) or ""
        method_name = match.group(3)
        body_indent = match.group(4)
        method_body = match.group(5)
        
        if '"""' in method_body or "'''" in method_body:
            return match.group(0)  # Уже есть docstring
        
        docstring = f'{body_indent}"""\n{body_indent}{method_name.replace("_", " ").title()}.\n{body_indent}"""\n'
        return f"{indent}{async_keyword}def {method_name}{match.group(0).split(':', 1)[1].split('\\n', 1)[0]}:\n{docstring}{body_indent}{method_body}"
    
    return re.sub(method_pattern, add_docstring, content, flags=re.MULTILINE | re.DOTALL)

def fix_unused_variables(content):
    """
    Исправляет проблемы с неиспользуемыми переменными.
    """
    # Добавляем _ prefix к неиспользуемым переменным в exception handlers
    content = re.sub(r'except\s+(\w+Exception)\s+as\s+(\w+):', r'except \1 as _\2:', content)
    content = re.sub(r'except\s+Exception\s+as\s+(\w+):', r'except Exception as _\1:', content)
    
    return content

def fix_broad_exceptions(content):
    """
    Исправляет проблемы с broad exception catching.
    """
    # Заменяем общие Exception на более специфичные где возможно
    patterns = [
        (r'except Exception as (\w+):', r'except (OSError, ValueError, TypeError) as \1:'),
        (r'except Exception:', r'except (OSError, ValueError, TypeError):'),
    ]
    
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    
    return content

def apply_fixes(filepath):
    """
    Применяет все исправления к файлу.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    
    # Применяем исправления
    content = fix_no_else_return(content)
    content = fix_unused_variables(content)
    # content = fix_broad_exceptions(content)  # Отключено, т.к. может сломать логику
    # content = fix_missing_docstrings(content)  # Отключено, т.к. слишком агрессивно
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Applied quality fixes to {filepath}")
        return True
    else:
        print(f"ℹ️  No quality fixes needed in {filepath}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fix_quality.py <filepath>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    apply_fixes(filepath)
