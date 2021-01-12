# -*- coding: utf-8 -*-

from marshmallow import class_registry
from marshmallow.base import SchemaABC
from marshmallow_jsonapi.fields import Relationship

from flask_rest_jsonapi.exceptions import InvalidField, InvalidInclude


def compute_schema(schema_cls, default_kwargs, qs, include):
    """Compute a schema around compound documents and sparse fieldsets

    :param Schema schema_cls: the schema class
    :param dict default_kwargs: the schema default kwargs
    :param QueryStringManager qs: qs
    :param list include: the relation field to include data from

    :return Schema schema: the schema computed
    """
    # manage include_data parameter of the schema
    schema_kwargs = default_kwargs
    schema_kwargs['include_data'] = schema_kwargs.get('include_data', tuple())

    if include:
        for include_path in include:
            field = include_path.split('.')[0]
            if field not in schema_cls._declared_fields:
                raise InvalidInclude("{} has no attribute {}".format(schema_cls.__name__, field))
            elif not isinstance(schema_cls._declared_fields[field], Relationship):
                raise InvalidInclude("{} is not a relationship attribute of {}".format(field, schema_cls.__name__))
            schema_kwargs['include_data'] += (field, )

    # make sure id field is in only parameter unless marshamllow will raise an Exception
    if schema_kwargs.get('only') is not None and 'id' not in schema_kwargs['only']:
        schema_kwargs['only'] += ('id',)

    # create base schema instance
    schema = schema_cls(**schema_kwargs)

    # manage sparse fieldsets
    if schema.opts.type_ in qs.fields:
        # check that sparse fieldsets exists in the schema
        for field in qs.fields[schema.opts.type_]:
            if field not in schema.declared_fields:
                raise InvalidField("{} has no attribute {}".format(schema.__class__.__name__, field))

        tmp_only = set(schema.declared_fields.keys()) & set(qs.fields[schema.opts.type_])
        if schema.only:
            tmp_only &= set(schema.only)
        schema.only = tuple(tmp_only)

        # make sure again that id field is in only parameter unless marshamllow will raise an Exception
        if schema.only is not None and 'id' not in schema.only:
            schema.only += ('id',)

    # manage compound documents
    if include:
        for include_path in include:
            field = include_path.split('.')[0]
            relation_field = schema.declared_fields[field]
            related_schema_cls = schema.declared_fields[field].__dict__['_Relationship__schema']
            related_schema_kwargs = {}
            if isinstance(related_schema_cls, SchemaABC):
                related_schema_kwargs['many'] = related_schema_cls.many
                related_schema_kwargs['include_data'] = related_schema_cls.__dict__.get('include_data')
                related_schema_cls = related_schema_cls.__class__
            if isinstance(related_schema_cls, str):
                related_schema_cls = class_registry.get_class(related_schema_cls)
            if '.' in include_path:
                related_include = ['.'.join(include_path.split('.')[1:])]
            else:
                related_include = None
            related_schema = compute_schema(related_schema_cls, related_schema_kwargs, qs, related_include)
            relation_field.__dict__['_Relationship__schema'] = related_schema

    return schema


def get_model_field(schema, field):
    """Get the model field of a schema field

    :param Schema schema: a marshmallow schema
    :param str field: the name of the schema field
    :return str: the name of the field in the model
    """
    if schema._declared_fields[field].attribute is not None:
        return schema._declared_fields[field].attribute
    return field


def get_relationships(schema):
    """Return relationship mapping from schema to model

    :param Schema schema: a marshmallow schema
    :param list: list of dict with schema field and model field
    """
    return {get_model_field(schema, key): key for (key, value) in schema._declared_fields.items()
            if isinstance(value, Relationship)}
