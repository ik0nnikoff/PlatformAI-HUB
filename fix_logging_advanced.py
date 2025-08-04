#!/usr/bin/env python3
"""
Улучшенный скрипт для исправления f-string интерполяции в логировании.
"""

import re
import sys

def fix_complex_logging_patterns(content):
    """
    Исправляет сложные случаи логирования с f-строками.
    """
    
    # Паттерн 1: logger.method(f"text {var}")
    pattern1 = r'((?:self\.)?logger\.\w+)\(f"([^"]*?)"\)'
    
    def replace_simple_fstring(match):
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
    
    # Паттерн 2: logger.method(f"text {var}", exc_info=True)
    pattern2 = r'((?:self\.)?logger\.\w+)\(f"([^"]*?)"\s*,\s*(.*?)\)'
    
    def replace_complex_fstring(match):
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
    
    # Применяем замены
    content = re.sub(pattern2, replace_complex_fstring, content)
    content = re.sub(pattern1, replace_simple_fstring, content)
    
    return content

def fix_logging_in_file(filepath):
    """
    Исправляет f-string интерполяцию в файле для логирования.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    content = fix_complex_logging_patterns(content)
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Fixed complex logging patterns in {filepath}")
        return True
    else:
        print(f"ℹ️  No complex logging fixes needed in {filepath}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fix_logging_advanced.py <filepath>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    fix_logging_in_file(filepath)
