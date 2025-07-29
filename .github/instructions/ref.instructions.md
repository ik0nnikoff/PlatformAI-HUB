Используй системные инструкции.

План текущих работ - MD/voice_v2_detailed_plan.md
Чеклист текущих работ - MD/voice_v2_checklist.md
Предполагаемая файловая структура по плану текущих работ - MD/voice_v2_file_structure.md
Изучи эти файлы.

В данный момент надо реализовать новый компонент по пунктам по чеклисту (MD/voice_v2_checklist.md) начиная с первого (фаза 1, пункт 1.1.1 и т.д.). План рассматривать только как справочный материал (MD/voice_v2_detailed_plan.md). После завершения каждой фазы проверяй работоспособность созданного/измененного кода. После выполнения каждой фазы и подфазы создай отчет по шаблону (MD/templates/voice_refactoring_report_template.md) - нейминг файла: Phase_PhaseNumber_report.md
Отмечай в чек-лите (MD/voice_v2_checklist.md) выполненные пункты по шаблону (MD/templates/project_checklist_template.md). Не меняй их, только отмечай.

В проекте используется uv, python запускай через uv run

### **Целевые метрики**:
- **Количество файлов**: ≤50 файлов (vs 113 в current)
- **Строки кода**: ≤15,000 строк (vs ~50,000 в current)
- **Производительность STT**: не хуже app/services/voice +10%
- **Производительность TTS**: не хуже app/services/voice +10%
- **Качество кода**: Pylint 9.5+/10
- **Unit test coverage**: 100% всех компонентов voice_v2
- **LangGraph workflow tests**: Полное покрытие voice интеграции
- **Architecture compliance**: SOLID, CCN<8, методы≤50 строк
- **Не должно быть неиспользуемых импортов**
- **Размер одного файла не должен превышать 600 строк**

продолжай работу учитывая:

Phase_1_3_1_architecture_review.md → LSP compliance для provider interfaces
Phase_1_1_4_architecture_patterns.md → успешные patterns из app/services/voice
Phase_1_2_3_performance_optimization.md → async patterns и connection pooling
Phase_1_2_2_solid_principles.md → Interface Segregation в provider design
MD/Phase_3_4_Architecture_Correction_Report.md
MD/Voice_v2_LangGraph_Decision_Analysis.md