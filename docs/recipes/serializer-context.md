# Customise serializer context

DRF's `get_serializer_context()` lives on every `GenericAPIView` and
returns `{"request", "view", "format"}` by default. When the standard
viewset shape uses one serializer per action it's enough — but service-
backed views run **two** serializers per request (input and output), and
sometimes those two need different context. The library plumbs that
without forcing you to override `get_serializer_context()` and branch on
direction.

Three layers, later wins on overlap:

1. **DRF default** — `view.get_serializer_context()`. Always applied.
2. **Directional fallback** — `get_input_serializer_context()` /
   `get_output_serializer_context()` on the view. Skipped when the method
   is absent, so plain DRF viewsets work unchanged.
3. **Per-action override** — `get_<action>_input_serializer_context()` /
   `get_<action>_output_serializer_context()`. Viewset-only; skipped on
   standalone single-purpose views. Has the final say.

The resolver is wired into service-backed views, viewset mixins,
`@service_action`, and `@selector_action`. Standalone `SelectorListView`
/ `SelectorRetrieveView` already receive DRF's context through
`self.get_serializer(...)` and continue to do so.

## Different context for input vs. output

A common case: the input serializer needs a "current tenant" for
cross-field validation, but the output serializer doesn't.

```python
class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorOutputSerializer,
        ),
    }

    def get_input_serializer_context(self):
        return {"tenant": self.request.tenant}
```

Inside the input serializer:

```python
class CreateAuthorInput(serializers.Serializer):
    name = serializers.CharField()

    def validate_name(self, value):
        tenant = self.context["tenant"]
        if Author.objects.filter(tenant=tenant, name=value).exists():
            raise serializers.ValidationError("name already taken in this tenant")
        return value
```

The output serializer keeps the default DRF context.

## Per-action override

When only one action needs the extra key, name the hook after the
action — no branching on `self.action`:

```python
class AuthorViewSet(ServiceViewSet):
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorOutputSerializer,
        ),
        "update": ServiceSpec(
            service=update_author,
            input_serializer=UpdateAuthorInput,
            output_serializer=AuthorOutputSerializer,
        ),
    }

    def get_create_input_serializer_context(self):
        return {"signup_token": self.request.headers.get("X-Signup-Token")}
```

`update` keeps the directional / DRF default; `create` adds the token on
top.

## On standalone views

Standalone mutation views (`ServiceCreateView`, `ServiceUpdateView`,
`ServiceDeleteView`) and `@service_action` honour the directional hooks
(`get_input_serializer_context` / `get_output_serializer_context`). The
per-action hooks only apply where there is an action — i.e. inside
viewset mixins and `@service_action` / `@selector_action`.
