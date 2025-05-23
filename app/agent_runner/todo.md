**Задача:**

Мне необходимо произвести качественный рефакторинг этой части приложения (графа агентов). Необходимо переместить код фабрики графов в папку langgraph разбить код на составные части для реализации удобной структуры, удобства дополнения, редактирования и расширения функционала. Выделяй код в функции и методы. Необходимо сохранить неизменность структуры входных и выходных данных, файл вызывающий граф (agent_runner) стараться не менять или менять минимально (вызов графа).
Не меняй в коде ни чего (логику, именование и алгоритмы) напрямую не связанного с поставленной задачей.
Требования к коду: ООП, удобная структура проекта, удобство дополнения, редактирования и расширения функционала. Разделение кода по классам и функционалу по файлам (каталогам). Не дублировать код, писать унифицированно, достигать консистентности.

В папке backup/agent_runner/ находятся изначальные версии файлов до рефакторинга (архив, его не изменять), что-бы ты мог ориентироваться в проделанных изменениях относительно оригинала.

**План рефакторинга (Вариант 1):**

- [x] **Этап 1: Подготовка структуры папок и файлов**
    - [x] Создать папку `app/agent_runner/langgraph/`.
    - [x] Создать `app/agent_runner/langgraph/__init__.py`.
    - [x] Переместить `app/agent_runner/langgraph_models.py` в `app/agent_runner/langgraph/models.py`.
    - [x] Переместить `app/agent_runner/langgraph_tools.py` в `app/agent_runner/langgraph/tools.py`.
    - [x] Создать файл `app/agent_runner/langgraph/factory.py`.
    - [x] Обновить импорты в `agent_runner.py`, `app/agent_runner/langgraph_factory.py` (старый) и других затронутых файлах после перемещения.

- [x] **Этап 2: Создание класса `GraphFactory` в `langgraph/factory.py`**
    - [x] Определить класс `GraphFactory`.
    - [x] Реализовать метод `__init__(self, agent_config, agent_id, logger_adapter)`.
    - [x] Перенести логику `_get_llm` из `langgraph_factory.py` в метод `_configure_llm(self)` класса `GraphFactory`.
    - [x] Создать метод `_configure_tools(self)`, который будет вызывать `configure_tools` из `langgraph.tools`.
    - [x] Перенести логику построения системного промпта в метод `_build_system_prompt(self)`.
    - [x] Перенести определения узлов (`agent_node`, `grade_documents_node`, `rewrite_node`, `generate_node`) как приватные методы класса `GraphFactory` (например, `_agent_node`, `_grade_documents_node` и т.д.). Обеспечить передачу необходимых зависимостей (например, `llm`, `tools`) через `self`.
    - [x] Перенести определения ребер (`decide_to_generate`, `route_tools`) как приватные методы класса `GraphFactory` (например, `_decide_to_generate_edge`, `_route_tools_edge`).
    - [x] Реализовать основной метод построения графа `create_graph(self) -> Tuple[Any, Dict[str, Any]]` внутри `GraphFactory`.

- [x] **Этап 3: Адаптация `app/agent_runner/langgraph_factory.py` (старый файл)**
    - [x] Изменить функцию `create_agent_app` так, чтобы она:
        - Импортировала `GraphFactory` из `app.agent_runner.langgraph.factory`.
        - Создавала экземпляр `GraphFactory`, передавая `agent_config`, `agent_id` и `logger_adapter` (логгер нужно будет получить или передать в `create_agent_app`).
        - Вызывала метод `factory_instance.create_graph()` для получения графа и `static_state_config`.
        - Возвращала полученные граф и `static_state_config`.
    - [x] Удалить из `app/agent_runner/langgraph_factory.py` весь код, который был перенесен в `GraphFactory`.

- [x] **Этап 4: Тестирование и проверка**
    - [x] Убедиться, что `agent_runner.py` корректно вызывает обновленную `create_agent_app` и получает граф.
    - [x] Проверить, что функциональность агента не изменилась.
    - [x] Проверить логирование на всех этапах.

- [x] **Этап 5: Финальные штрихи**
    - [x] Проверить код на соответствие требованиям (ООП, структура, консистентность).
    - [x] Удалить старые, неиспользуемые файлы (`app/agent_runner/langgraph_models.py`, `app/agent_runner/langgraph_tools.py` - исходные файлы до перемещения).
    - [x] Обновить `todo.md` по завершении всех пунктов.
