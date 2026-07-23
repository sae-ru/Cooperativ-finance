# Языки интерфейса / Interface languages

[Русский](#русский) | [English](#english)

## Русский

Frontend автоматически читает все файлы `*.xml` из этой папки во время сборки. Чтобы добавить язык:

1. Скопируйте `en.xml` под новым именем, например `es.xml`.
2. Измените атрибуты `code` и `label` корневого элемента `<locale>`.
3. Переведите значения каждого `<message>`, не меняя ключи `key`.
4. Переведите `<value>` для отображения статусов, ролей, типов операций и других стабильных кодов API. Атрибут `code` менять нельзя.
5. Переведите `<phrase>` для составных подписей и старых экранов, которые ещё не переведены на ключи.
6. Запустите frontend-тесты и сборку.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<locale code="es" label="Español">
  <messages>
    <message key="common.language">Idioma</message>
    <message key="language.es">Español</message>
  </messages>
  <values>
    <value code="ACTIVE">Activo</value>
  </values>
  <phrases>
    <phrase source="Выйти">Salir</phrase>
  </phrases>
</locale>
```

`ru.xml` является обязательным запасным языком. Набор ключей `<message>` и кодов `<value>` во всех языковых файлах должен совпадать. Машинные коды в API, базе, журнале, подписях и хешах остаются неизменными; словарь `<value>` меняет только их отображение человеку. Элементы `code`, `pre`, `script`, `style` и `textarea` автоматически не переводятся.

## English

The frontend discovers every `*.xml` file in this directory during the build. To add a language:

1. Copy `en.xml` to a new filename such as `es.xml`.
2. Change the root `<locale>` attributes `code` and `label`.
3. Translate every `<message>` value without changing its `key`.
4. Translate every `<value>` used to display API statuses, roles, operation types, and other stable codes. Do not change the `code` attribute.
5. Translate `<phrase>` entries for composite labels and legacy screens that have not moved to message keys yet.
6. Run the frontend tests and build.

`ru.xml` is the required fallback locale. Every file must contain the same `<message>` key set and `<value>` code set. API, database, audit, signature, and hash values remain unchanged; `<value>` only controls their human-readable presentation. The `code`, `pre`, `script`, `style`, and `textarea` elements are intentionally excluded from automatic translation.