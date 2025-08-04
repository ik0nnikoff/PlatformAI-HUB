#!/usr/bin/env python3
"""
Финальный скрипт для исправления всех f-string проблем в логировании.
"""

import re
import sys

def fix_all_fstring_logging(content):
    """
    Исправляет все f-string конструкции в логировании.
    """
    
    # Список всех возможных паттернов логирования
    patterns = [
        # Простые f-строки
        (r'((?:self\.)?logger\.\w+)\(f"([^"]*?)"\)', lambda m: fix_simple_fstring(m)),
        # F-строки с дополнительными параметрами
        (r'((?:self\.)?logger\.\w+)\(f"([^"]*?)"\s*,\s*([^)]+)\)', lambda m: fix_complex_fstring(m)),
        # Многострочные f-строки
        (r'((?:self\.)?logger\.\w+)\(\s*f"([^"]*?)"\s*\)', lambda m: fix_simple_fstring(m)),
    ]
    
    for pattern, fix_func in patterns:
        content = re.sub(pattern, fix_func, content, flags=re.MULTILINE | re.DOTALL)
    
    return content

def fix_simple_fstring(match):
    """Исправляет простые f-строки в логировании."""
    logger_call = match.group(1)
    fstring_content = match.group(2)
    
    # Находим все {variable} в f-строке
    variables = []
    def extract_var(var_match):
        var_name = var_match.group(1)
        variables.append(var_name)
        return '%s'
    
    message = re.sub(r'\{([^}]+)\}', extract_var, fstring_content)
    
    if variables:
        vars_str = ', '.join(variables)
        return f'{logger_call}("{message}", {vars_str})'
    else:
        return f'{logger_call}("{message}")'

def fix_complex_fstring(match):
    """Исправляет f-строки с дополнительными параметрами."""
    logger_call = match.group(1)
    fstring_content = match.group(2)
    additional_args = match.group(3)
    
    variables = []
    def extract_var(var_match):
        var_name = var_match.group(1)
        variables.append(var_name)
        return '%s'
    
    message = re.sub(r'\{([^}]+)\}', extract_var, fstring_content)
    
    if variables:
        vars_str = ', '.join(variables)
        return f'{logger_call}("{message}", {vars_str}, {additional_args})'
    else:
        return f'{logger_call}("{message}", {additional_args})'

def apply_final_fixes(filepath):
    """
    Применяет финальные исправления к файлу.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    content = fix_all_fstring_logging(content)
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Applied final logging fixes to {filepath}")
        return True
    else:
        print(f"ℹ️  No final logging fixes needed in {filepath}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fix_final.py <filepath>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    apply_final_fixes(filepath)
