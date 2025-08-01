Используй системные инструкции.

План текущих работ - MD/10_voice_v2_optimization_detailed_plan.md
Чеклист текущих работ - MD/11_voice_v2_optimization_checklist.md
Изучи эти файлы.

В данный момент надо реализовать новый компонент по пунктам по чеклисту (MD/11_voice_v2_optimization_checklist.md) начиная с первого (фаза 1, пункт 1.1.1 и т.д.). План рассматривать только как справочный материал (MD/10_voice_v2_optimization_detailed_plan.md). После завершения каждой фазы проверяй работоспособность созданного/измененного кода. После выполнения каждой фазы и подфазы создай отчет по шаблону (MD/templates/voice_refactoring_report_template.md) - нейминг файла: Phase_PhaseNumber_report.md в папке MD/Reports
Отмечай в чек-лите (MD/11_voice_v2_optimization_checklist.md) выполненные пункты по шаблону (MD/templates/project_checklist_template.md). Не меняй их, только отмечай.

В проекте используется uv, python запускай через uv run

### **Целевые метрики**:
- **Качество кода**: Pylint 9.5+/10
- **Architecture compliance**: SOLID, CCN<8, методы≤50 строк
- **Не должно быть неиспользуемых импортов**
- **Размер одного файла не должен превышать 600 строк**
