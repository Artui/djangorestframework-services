# Services

## Protocols

Each Protocol is parameterised by input, instance (where applicable), result,
and a trailing extras `TypedDict`. The extras parameter defaults to a private
arbitrary-key shape — passing two type arguments gives the lenient form
(`CreateService[AuthorIn, Author]`), passing all three switches to the strict
form (`CreateService[AuthorIn, Author, MyExtras]`).

::: rest_framework_services.services.create_service.CreateService

::: rest_framework_services.services.update_service.UpdateService

::: rest_framework_services.services.delete_service.DeleteService

## Default model service factories

### `create_model`

::: rest_framework_services.services.create_model.create_model

### `update_model`

::: rest_framework_services.services.update_model.update_model

### `delete_model`

::: rest_framework_services.services.delete_model.delete_model

### `acreate_model`

::: rest_framework_services.services.acreate_model.acreate_model

### `aupdate_model`

::: rest_framework_services.services.aupdate_model.aupdate_model

### `adelete_model`

::: rest_framework_services.services.adelete_model.adelete_model

## Decorators

::: rest_framework_services.implements.implements

## Helpers

### `call_service`

::: rest_framework_services.services.call_service.call_service

### `acall_service`

::: rest_framework_services.services.acall_service.acall_service
