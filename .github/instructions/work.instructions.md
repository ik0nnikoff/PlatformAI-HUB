Используй системные инструкции.

Будь внимателен к деталям, анализируй комплексно, выявляй связи и зависимости, учитывай контекст.
В проекте используется uv, python запускай через uv run

### **Целевые метрики**:
- **Качество кода**: Pylint 9.5+/10
- **Architecture compliance**: SOLID, CCN<8, методы≤50 строк
- **Не должно быть неиспользуемых импортов**
- **Размер одного файла не должен превышать 600 строк**

используй codacy, pylint, flake8, mypy, black, isort для проверки кода
Проверяй циклическую сложность с помощью lizard

Работаем по чеклисту: MD/36_process_manager_refactoring_checklist.md
Отчеты после выполнения фаз по шаблону: MD/templates/voice_refactoring_report_template.md

Копия исходного файла: app/services/process_manager.backup.py