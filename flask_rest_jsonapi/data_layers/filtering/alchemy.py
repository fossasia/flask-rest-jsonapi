# -*- coding: utf-8 -*-

from sqlalchemy import and_, or_, not_

from flask import current_app
from flask_rest_jsonapi.exceptions import InvalidFilters
from flask_rest_jsonapi.schema import get_relationships, get_model_field


def create_filters(model, filter_info, resource):
    """Apply filters from filters information to base query

    :param DeclarativeMeta model: the model of the node
    :param dict filter_info: current node filter information
    :param Resource resource: the resource
    """
    filters = []
    for filter_ in filter_info:
        filters.append(Node(model, filter_, resource, resource.schema).resolve())

    return filters


class Node(object):

    def __init__(self, model, filter_, resource, schema):
        self.model = model
        self.filter_ = filter_
        self.resource = resource
        self.schema = schema

    def resolve(self):
        if 'or' not in self.filter_ and 'and' not in self.filter_ and 'not' not in self.filter_:
            value = self.value

            if isinstance(value, dict):
                value = Node(self.related_model, value, self.resource, self.related_schema).resolve()

            if '__' in self.filter_.get('name', ''):
                value = {self.filter_['name'].split('__')[1]: value}

            if isinstance(value, dict):
                return getattr(self.column, self.operator)(**value)
            else:
                return getattr(self.column, self.operator)(value)

        if 'or' in self.filter_:
            return or_(Node(self.model, filt, self.resource, self.schema).resolve() for filt in self.filter_['or'])
        if 'and' in self.filter_:
            return and_(Node(self.model, filt, self.resource, self.schema).resolve() for filt in self.filter_['and'])
        if 'not' in self.filter_:
            return not_(Node(self.model, self.filter_['not'], self.resource, self.schema).resolve())

    @property
    def name(self):
        """Return the name of the node or raise a BadRequest exception

        :return str: the name of the field to filter on
        """
        name = self.filter_.get('name')

        if name is None:
            raise InvalidFilters("Can't find name of a filter")

        if '__' in name:
            name = name.split('__')[0]

        if current_app.config.get('DASHERIZE_API') == True:
            name = name.replace('-', '_')

        if name not in self.schema._declared_fields:
            raise InvalidFilters("{} has no attribute {}".format(self.schema.__name__, name))

        return name

    @property
    def op(self):
        """Return the operator of the node

        :return str: the operator to use in the filter
        """
        try:
            return self.filter_['op']
        except KeyError:
            raise InvalidFilters("Can't find op of a filter")

    @property
    def column(self):
        """Get the column object

        :param DeclarativeMeta model: the model
        :param str field: the field
        :return InstrumentedAttribute: the column to filter on
        """
        field = self.name

        model_field = get_model_field(self.schema, field)

        try:
            return getattr(self.model, model_field)
        except AttributeError:
            raise InvalidFilters("{} has no attribute {}".format(self.model.__name__, model_field))

    @property
    def operator(self):
        """Get the function operator from his name

        :return callable: a callable to make operation on a column
        """
        operators = (self.op, self.op + '_', '__' + self.op + '__')

        for op in operators:
            if hasattr(self.column, op):
                return op

        raise InvalidFilters("{} has no operator {}".format(self.column.key, self.op))

    @property
    def value(self):
        """Get the value to filter on

        :return: the value to filter on
        """
        if self.filter_.get('field') is not None:
            try:
                result = getattr(self.model, self.filter_['field'])
            except AttributeError:
                raise InvalidFilters("{} has no attribute {}".format(self.model.__name__, self.filter_['field']))
            else:
                return result
        else:
            if 'val' not in self.filter_:
                raise InvalidFilters("Can't find value or field in a filter")

            return self.filter_['val']

    @property
    def related_model(self):
        """Get the related model of a relationship field

        :return DeclarativeMeta: the related model
        """
        relationship_field = self.name

        if relationship_field not in get_relationships(self.schema).values():
            raise InvalidFilters("{} has no relationship attribute {}".format(self.schema.__name__, relationship_field))

        relationship_model_field = get_model_field(self.schema, relationship_field)

        return getattr(self.model, relationship_model_field).property.mapper.class_

    @property
    def related_schema(self):
        """Get the related schema of a relationship field

        :return Schema: the related schema
        """
        relationship_field = self.name

        if relationship_field not in get_relationships(self.schema).values():
            raise InvalidFilters("{} has no relationship attribute {}".format(self.schema.__name__, relationship_field))

        return self.schema._declared_fields[relationship_field].schema.__class__
