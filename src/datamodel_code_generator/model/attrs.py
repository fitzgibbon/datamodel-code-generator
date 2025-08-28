from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Optional

from datamodel_code_generator import DatetimeClassType, PythonVersion, PythonVersionMin
from datamodel_code_generator.imports import (
    IMPORT_DATE,
    IMPORT_DATETIME,
    IMPORT_TIME,
    IMPORT_TIMEDELTA,
    Import,
)
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model.base import UNDEFINED
from datamodel_code_generator.model.imports import (
    IMPORT_ATTRS_DEFINE,
    IMPORT_ATTRS_FIELD,
    IMPORT_ATTRS_VALIDATORS,
)
from datamodel_code_generator.model.types import DataTypeManager as _DataTypeManager
from datamodel_code_generator.model.types import type_map_factory
from datamodel_code_generator.types import DataType, StrictTypes, Types, chain_as_tuple

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Sequence
    from pathlib import Path

    from datamodel_code_generator.reference import Reference


def _has_field_assignment(field: DataModelFieldBase) -> bool:
    return bool(field.field) or not (
        field.required or (field.represented_default == "None" and field.strip_default_none)
    )


class DataClass(DataModel):
    TEMPLATE_FILE_PATH: ClassVar[str] = "attrs.jinja2"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_ATTRS_DEFINE,)

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        frozen: bool = False,
        treat_dot_as_module: bool = False,
    ) -> None:
        super().__init__(
            reference=reference,
            fields=sorted(fields, key=_has_field_assignment),
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            frozen=frozen,
            treat_dot_as_module=treat_dot_as_module,
        )


class DataModelField(DataModelFieldBase):
    _FIELD_KEYS: ClassVar[set[str]] = {
        "factory",  # mapped from default_factory
        "init",
        "repr",
        "eq",
        "order",
        "metadata",
        "kw_only",
    }
    constraints: Optional[Any] = None  # noqa: UP045

    @property
    def imports(self) -> tuple[Import, ...]:
        field = self.field
        extra_imports: list[Import] = []
        if field and field.startswith("field("):
            extra_imports.append(IMPORT_ATTRS_FIELD)
        # If validators are referenced in the field expression, import attrs.validators
        if field and "validators." in field:
            extra_imports.append(IMPORT_ATTRS_VALIDATORS)
        return chain_as_tuple(super().imports, tuple(extra_imports))

    def self_reference(self) -> bool:  # pragma: no cover
        return isinstance(self.parent, DataClass) and self.parent.reference.path in {
            d.reference.path for d in self.data_type.all_data_types if d.reference
        }

    @property
    def field(self) -> str | None:
        """for backwards compatibility"""
        result = str(self)
        if not result:
            return None
        return result

    def __str__(self) -> str:
        # Start with extras relevant for attrs.field
        data: dict[str, Any] = {k: v for k, v in self.extras.items() if k in self._FIELD_KEYS}

        # Map default_factory -> factory for attrs
        if "default_factory" in self.extras:
            data["factory"] = self.extras["default_factory"]

        if self.default != UNDEFINED and self.default is not None:
            data["default"] = self.default

        if self.required:
            data = {k: v for k, v in data.items() if k not in {"default", "factory"}}

        # Build validators list from constraints and attach metadata
        validators: list[str] = []
        constraints: dict[str, Any] = {}
        if isinstance(self.constraints, dict):
            constraints = self.constraints

        # string/sequence length constraints
        min_length = constraints.get("minLength")
        max_length = constraints.get("maxLength")
        min_items = constraints.get("minItems")
        max_items = constraints.get("maxItems")
        if min_length is not None or max_length is not None:
            args = []
            if min_length is not None:
                args.append(f"min={int(min_length)}")
            if max_length is not None:
                args.append(f"max={int(max_length)}")
            validators.append(f"validators.length({', '.join(args)})")
        if min_items is not None or max_items is not None:
            args = []
            if min_items is not None:
                args.append(f"min={int(min_items)}")
            if max_items is not None:
                args.append(f"max={int(max_items)}")
            validators.append(f"validators.length({', '.join(args)})")

        # numeric comparisons
        # JSON Schema uses: minimum, maximum, exclusiveMinimum, exclusiveMaximum
        num_map = (
            ("minimum", "ge"),
            ("exclusiveMinimum", "gt"),
            ("maximum", "le"),
            ("exclusiveMaximum", "lt"),
        )
        for key, fn in num_map:
            value = constraints.get(key)
            if value is not None:
                validators.append(f"validators.{fn}({value})")

        # regex pattern
        regex = constraints.get("pattern")
        if regex:
            validators.append(f"validators.matches_re({regex!r})")

        if validators:
            data["validator"] = f"[{', '.join(validators)}]"

        # Metadata: include description/title/examples from schema
        metadata: dict[str, Any] = {}
        description = self.extras.get("description")
        title = self.extras.get("title")
        examples = self.extras.get("examples") or self.extras.get("example")
        if description is not None:
            metadata["description"] = description
        if title is not None:
            metadata["title"] = title
        if examples is not None:
            metadata["examples"] = examples
        if metadata:
            data["metadata"] = metadata

        # If still empty, nothing to emit
        if not data:
            return ""

        # Only default present -> emit just default representation
        if len(data) == 1 and "default" in data:
            default = data["default"]
            if isinstance(default, (list, dict)):
                return f"field(factory=lambda :{default!r})"
            return repr(default)

        kwargs = [
            f"{k}={v if k in {'factory', 'validator'} else repr(v)}" for k, v in data.items()
        ]
        return f"field({', '.join(kwargs)})"


class DataTypeManager(_DataTypeManager):
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        python_version: PythonVersion = PythonVersionMin,
        use_standard_collections: bool = False,  # noqa: FBT001, FBT002
        use_generic_container_types: bool = False,  # noqa: FBT001, FBT002
        strict_types: Sequence[StrictTypes] | None = None,
        use_non_positive_negative_number_constrained_types: bool = False,  # noqa: FBT001, FBT002
        use_union_operator: bool = False,  # noqa: FBT001, FBT002
        use_pendulum: bool = False,  # noqa: FBT001, FBT002
        target_datetime_class: DatetimeClassType = DatetimeClassType.Datetime,
        treat_dot_as_module: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        super().__init__(
            python_version,
            use_standard_collections,
            use_generic_container_types,
            strict_types,
            use_non_positive_negative_number_constrained_types,
            use_union_operator,
            use_pendulum,
            target_datetime_class,
            treat_dot_as_module,
        )

        datetime_map = (
            {
                Types.time: self.data_type.from_import(IMPORT_TIME),
                Types.date: self.data_type.from_import(IMPORT_DATE),
                Types.date_time: self.data_type.from_import(IMPORT_DATETIME),
                Types.timedelta: self.data_type.from_import(IMPORT_TIMEDELTA),
            }
            if target_datetime_class is DatetimeClassType.Datetime
            else {}
        )

        self.type_map: dict[Types, DataType] = {
            **type_map_factory(self.data_type),
            **datetime_map,
        }
