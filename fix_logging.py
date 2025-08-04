#!/usr/bin/env python3
"""
Скрипт для автоматического исправления f-string интерполяции в логировании.
Заменяет конструкции вида logger.method(f"text {var}") на logger.method("text %s", var)
"""

import re
import sys

def fix_logging_in_file(filepath):
    """
    Исправляет f-string интерполяцию в файле для логирования.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    fixes_count = 0
    
    # Паттерн для поиска logger.method(f"...") с многострочным f-string
    # Поддерживает self.logger.method и просто logger.method
    logger_pattern = r'((?:self\.)?logger\.\w+)\(\s*f"([^"]*?)"\s*\)'
    
    def replace_fstring(match):
        nonlocal fixes_count
        logger_call = match.group(1)
        fstring_content = match.group(2)
        
        # Заменяем {variable} на %s и извлекаем переменные
        variables = []
        def extract_var(var_match):
            var_name = var_match.group(1)
            variables.append(var_name)
            return '%s'
        
        # Находим все {variable} в f-строке
        message = re.sub(r'\{([^}]+)\}', extract_var, fstring_content)
        
        # Формируем новый вызов
        if variables:
            vars_str = ', '.join(variables)
            result = f'{logger_call}("{message}", {vars_str})'
        else:
            result = f'{logger_call}("{message}")'
        
        fixes_count += 1
        return result
    
    # Применяем замену
    content = re.sub(logger_pattern, replace_fstring, content, flags=re.MULTILINE | re.DOTALL)
    
    # Дополнительный паттерн для случаев с переносами строк
    multiline_pattern = r'((?:self\.)?logger\.\w+)\(\s*f"""([^"]*)"""\s*\)'
    content = re.sub(multiline_pattern, lambda m: replace_fstring(m), content, flags=re.MULTILINE | re.DOTALL)
    
    # Еще один паттерн для одинарных кавычек
    single_quote_pattern = r"((?:self\.)?logger\.\w+)\(\s*f'([^']*)'\s*\)"
    content = re.sub(single_quote_pattern, lambda m: replace_fstring(m), content, flags=re.MULTILINE | re.DOTALL)
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Fixed {fixes_count} logging statements in {filepath}")
        return True
    else:
        print(f"ℹ️  No logging fixes needed in {filepath}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fix_logging.py <filepath>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    fix_logging_in_file(filepath)
