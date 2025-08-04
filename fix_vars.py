#!/usr/bin/env python3
"""
Скрипт для исправления логирования - заменяет {var} на %s и добавляет переменные
"""

import re
import sys

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Паттерн для нахождения строк с переменными в {}
    pattern = r'(\w+\.logger\.\w+\("[^"]*{[^}]+}[^"]*")\)'
    
    def replace_vars(match):
        line = match.group(1)
        
        # Извлекаем переменные из {}
        variables = re.findall(r'{([^}]+)}', line)
        
        # Заменяем {var} на %s
        for var in variables:
            line = line.replace(f'{{{var}}}', '%s')
        
        # Добавляем переменные в конец
        if variables:
            vars_str = ', ' + ', '.join(variables)
            line = line + vars_str + ')'
        else:
            line = line + ')'
        
        return line
    
    # Применяем замену
    new_content = re.sub(pattern, replace_vars, content)
    
    # Сохраняем файл
    with open(filepath, 'w') as f:
        f.write(new_content)
    
    print(f"Fixed {filepath}")

if __name__ == "__main__":
    fix_file(sys.argv[1])
