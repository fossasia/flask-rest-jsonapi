# -*- coding: utf-8 -*-

from flask import current_app
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.collections import InstrumentedList
from sqlalchemy.inspection import inspect
from flask import request
from flask_rest_jsonapi.constants import DEFAULT_PAGE_SIZE
from flask_rest_jsonapi.data_layers.base import BaseDataLayer
from flask_rest_jsonapi.exceptions import RelationNotFound, RelatedObjectNotFound, JsonApiException,\
    InvalidSort, ObjectNotFound
from flask_rest_jsonapi.data_layers.filtering.alchemy import create_filters
from flask_rest_jsonapi.schema import get_relationships


class SqlalchemyDataLayer(BaseDataLayer):

    def __init__(self, kwargs):
        super(SqlalchemyDataLayer, self).__init__(kwargs)

        if not hasattr(self, 'session'):
            raise Exception("You must provide a session in data_layer_kwargs to use sqlalchemy data layer in {}"
                            .format(self.resource.__name__))
        if not hasattr(self, 'model'):
            raise Exception("You must provide a model in data_layer_kwargs to use sqlalchemy data layer in {}"
                            .format(self.resource.__name__))

    def create_object(self, data, view_kwargs):
        """Create an object through sqlalchemy

        :param dict data: the data validated by marshmallow
        :param dict view_kwargs: kwargs from the resource view
        :return DeclarativeMeta: an object from sqlalchemy
        """
        self.before_create_object(data, view_kwargs)

        relationship_fields = get_relationships(self.resource.schema)
        obj = self.model(**{key: value for (key, value) in data.items() if key not in relationship_fields})
        self.apply_relationships(data, obj)

        self.session.add(obj)
        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            if current_app.config.get('PROPOGATE_ERROR') == True:
                raise JsonApiException({'pointer': '/data'}, "Object creation error: " + str(e))
            else:
                raise JsonApiException({'pointer': '/data'}, "Object creation error")

        self.after_create_object(obj, data, view_kwargs)

        return obj

    def get_object(self, view_kwargs, get_trashed=False):
        """Retrieve an object through sqlalchemy

        :params dict view_kwargs: kwargs from the resource view
        :return DeclarativeMeta: an object from sqlalchemy
        """
        self.before_get_object(view_kwargs)

        id_field = getattr(self, 'id_field', inspect(self.model).primary_key[0].name)
        try:
            filter_field = getattr(self.model, id_field)
        except Exception:
            raise Exception("{} has no attribute {}".format(self.model.__name__, id_field))

        url_field = getattr(self, 'url_field', 'id')
        filter_value = view_kwargs[url_field]
        try:
            if not getattr(self, 'resource', None) or 'deleted_at' not in self.resource.schema._declared_fields or get_trashed or current_app.config.get('SOFT_DELETE') != True:
                obj = self.session.query(self.model).filter(filter_field == filter_value).one()
            else:
                obj = self.session.query(self.model).filter(filter_field == filter_value).filter_by(deleted_at=None).one()
        except NoResultFound:
            obj = None

        self.after_get_object(obj, view_kwargs)

        return obj

    def get_collection(self, qs, view_kwargs):
        """Retrieve a collection of objects through sqlalchemy

        :param QueryStringManager qs: a querystring manager to retrieve information from url
        :param dict view_kwargs: kwargs from the resource view
        :return tuple: the number of object and the list of objects
        """
        self.before_get_collection(qs, view_kwargs)
        if 'deleted_at' not in self.resource.schema._declared_fields or request.args.get('get_trashed') == 'true' or current_app.config['SOFT_DELETE'] is False:
            query = self.query(view_kwargs)
        else:
            query = self.query(view_kwargs).filter(self.model.deleted_at == None)

        if qs.filters:
            query = self.filter_query(query, qs.filters, self.model)

        if qs.sorting:
            query = self.sort_query(query, qs.sorting)

        object_count = query.count()

        query = self.paginate_query(query, qs.pagination)

        collection = query.all()

        self.after_get_collection(collection, qs, view_kwargs)

        return object_count, collection

    def update_object(self, obj, data, view_kwargs):
        """Update an object through sqlalchemy

        :param DeclarativeMeta obj: an object from sqlalchemy
        :param dict data: the data validated by marshmallow
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if object have changed else False
        """
        if obj is None:
            url_field = getattr(self, 'url_field', 'id')
            filter_value = view_kwargs[url_field]
            raise ObjectNotFound({'parameter': url_field},
                                 '{}: {} not found'.format(self.model.__name__, filter_value))

        self.before_update_object(obj, data, view_kwargs)

        relationship_fields = get_relationships(self.resource.schema)
        for key, value in data.items():
            if hasattr(obj, key) and key not in relationship_fields:
                setattr(obj, key, value)

        self.apply_relationships(data, obj)

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            if current_app.config.get('PROPOGATE_ERROR') == True:
                raise JsonApiException({'pointer': '/data'}, "Update object error: " + str(e))
            else:
                raise JsonApiException({'pointer': '/data'}, "Update object error")

        self.after_update_object(obj, data, view_kwargs)

    def delete_object(self, obj, view_kwargs):
        """Delete an object through sqlalchemy

        :param DeclarativeMeta item: an item from sqlalchemy
        :param dict view_kwargs: kwargs from the resource view
        """
        if obj is None:
            url_field = getattr(self, 'url_field', 'id')
            filter_value = view_kwargs[url_field]
            raise ObjectNotFound({'parameter': url_field},
                                 '{}: {} not found'.format(self.model.__name__, filter_value))

        self.before_delete_object(obj, view_kwargs)

        self.session.delete(obj)
        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            if current_app.config.get('PROPOGATE_ERROR') == True:
                raise JsonApiException('', "Delete object error: " + str(e))
            else:
                raise JsonApiException('', "Delete object error")

        self.after_delete_object(obj, view_kwargs)

    def create_relationship(self, json_data, relationship_field, related_id_field, view_kwargs):
        """Create a relationship

        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if relationship have changed else False
        """
        self.before_create_relationship(json_data, relationship_field, related_id_field, view_kwargs)

        obj = self.get_object(view_kwargs)

        if obj is None:
            url_field = getattr(self, 'url_field', 'id')
            filter_value = view_kwargs[url_field]
            raise ObjectNotFound({'parameter': url_field},
                                 '{}: {} not found'.format(self.model.__name__, filter_value))

        if not hasattr(obj, relationship_field):
            raise RelationNotFound('', "{} has no attribute {}".format(obj.__class__.__name__, relationship_field))

        related_model = getattr(obj.__class__, relationship_field).property.mapper.class_

        updated = False

        if isinstance(json_data['data'], list):
            obj_ids = {str(getattr(obj__, related_id_field)) for obj__ in getattr(obj, relationship_field)}

            for obj_ in json_data['data']:
                if obj_['id'] not in obj_ids:
                    getattr(obj,
                            relationship_field).append(self.get_related_object(related_model, related_id_field, obj_))
                    updated = True
        else:
            related_object = None

            if json_data['data'] is not None:
                related_object = self.get_related_object(related_model, related_id_field, json_data['data'])

            obj_id = getattr(getattr(obj, relationship_field), related_id_field, None)
            new_obj_id = getattr(related_object, related_id_field, None)
            if obj_id != new_obj_id:
                setattr(obj, relationship_field, related_object)
                updated = True

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            if current_app.config.get('PROPOGATE_ERROR') == True:
                raise JsonApiException('', "Create relationship error: " + str(e))
            else:
                raise JsonApiException('', "Create relationship error")
        self.after_create_relationship(obj, updated, json_data, relationship_field, related_id_field, view_kwargs)

        return obj, updated

    def get_relationship(self, relationship_field, related_type_, related_id_field, view_kwargs):
        """Get a relationship

        :param str relationship_field: the model attribute used for relationship
        :param str related_type_: the related resource type
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return tuple: the object and related object(s)
        """
        self.before_get_relationship(relationship_field, related_type_, related_id_field, view_kwargs)

        obj = self.get_object(view_kwargs)

        if obj is None:
            url_field = getattr(self, 'url_field', 'id')
            filter_value = view_kwargs[url_field]
            raise ObjectNotFound({'parameter': url_field},
                                 '{}: {} not found'.format(self.model.__name__, filter_value))

        if not hasattr(obj, relationship_field):
            raise RelationNotFound('', "{} has no attribute {}".format(obj.__class__.__name__, relationship_field))

        related_objects = getattr(obj, relationship_field)

        if related_objects is None:
            return obj, related_objects

        self.after_get_relationship(obj, related_objects, relationship_field, related_type_, related_id_field,
                                    view_kwargs)

        if isinstance(related_objects, InstrumentedList):
            return obj,\
                [{'type': related_type_, 'id': getattr(obj_, related_id_field)} for obj_ in related_objects]
        else:
            return obj, {'type': related_type_, 'id': getattr(related_objects, related_id_field)}

    def update_relationship(self, json_data, relationship_field, related_id_field, view_kwargs):
        """Update a relationship

        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if relationship have changed else False
        """
        self.before_update_relationship(json_data, relationship_field, related_id_field, view_kwargs)

        obj = self.get_object(view_kwargs)

        if obj is None:
            url_field = getattr(self, 'url_field', 'id')
            filter_value = view_kwargs[url_field]
            raise ObjectNotFound({'parameter': url_field},
                                 '{}: {} not found'.format(self.model.__name__, filter_value))

        if not hasattr(obj, relationship_field):
            raise RelationNotFound('', "{} has no attribute {}".format(obj.__class__.__name__, relationship_field))

        related_model = getattr(obj.__class__, relationship_field).property.mapper.class_

        updated = False

        if isinstance(json_data['data'], list):
            related_objects = []

            for obj_ in json_data['data']:
                related_objects.append(self.get_related_object(related_model, related_id_field, obj_))

            obj_ids = {getattr(obj__, related_id_field) for obj__ in getattr(obj, relationship_field)}
            new_obj_ids = {getattr(related_object, related_id_field) for related_object in related_objects}
            if obj_ids != new_obj_ids:
                setattr(obj, relationship_field, related_objects)
                updated = True

        else:
            related_object = None

            if json_data['data'] is not None:
                related_object = self.get_related_object(related_model, related_id_field, json_data['data'])

            obj_id = getattr(getattr(obj, relationship_field), related_id_field, None)
            new_obj_id = getattr(related_object, related_id_field, None)
            if obj_id != new_obj_id:
                setattr(obj, relationship_field, related_object)
                updated = True

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            if current_app.config.get('PROPOGATE_ERROR') == True:
                raise JsonApiException('', "Update relationship error: " + str(e))
            else:
                raise JsonApiException('', "Update relationship error")
        self.after_update_relationship(obj, updated, json_data, relationship_field, related_id_field, view_kwargs)

        return obj, updated

    def delete_relationship(self, json_data, relationship_field, related_id_field, view_kwargs):
        """Delete a relationship

        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        """
        self.before_delete_relationship(json_data, relationship_field, related_id_field, view_kwargs)

        obj = self.get_object(view_kwargs)

        if obj is None:
            url_field = getattr(self, 'url_field', 'id')
            filter_value = view_kwargs[url_field]
            raise ObjectNotFound({'parameter': url_field},
                                 '{}: {} not found'.format(self.model.__name__, filter_value))

        if not hasattr(obj, relationship_field):
            raise RelationNotFound('', "{} has no attribute {}".format(obj.__class__.__name__, relationship_field))

        related_model = getattr(obj.__class__, relationship_field).property.mapper.class_

        updated = False

        if isinstance(json_data['data'], list):
            obj_ids = {str(getattr(obj__, related_id_field)) for obj__ in getattr(obj, relationship_field)}

            for obj_ in json_data['data']:
                if obj_['id'] in obj_ids:
                    getattr(obj,
                            relationship_field).remove(self.get_related_object(related_model, related_id_field, obj_))
                    updated = True
        else:
            setattr(obj, relationship_field, None)
            updated = True

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            if current_app.config.get('PROPOGATE_ERROR') == True:
                raise JsonApiException('', "Delete relationship error: " + str(e))
            else:
                raise JsonApiException('', "Delete relationship error")

        self.after_delete_relationship(obj, updated, json_data, relationship_field, related_id_field, view_kwargs)

        return obj, updated

    def get_related_object(self, related_model, related_id_field, obj):
        """Get a related object

        :param Model related_model: an sqlalchemy model
        :param str related_id_field: the identifier field of the related model
        :param DeclarativeMeta obj: the sqlalchemy object to retrieve related objects from
        :return DeclarativeMeta: a related object
        """
        try:
            related_object = self.session.query(related_model)\
                                         .filter(getattr(related_model, related_id_field) == obj['id'])\
                                         .one()
        except NoResultFound:
            raise RelatedObjectNotFound('', "{}.{}: {} not found".format(related_model.__name__,
                                                                         related_id_field,
                                                                         obj['id']))

        return related_object

    def apply_relationships(self, data, obj):
        """Apply relationship provided by data to obj

        :param dict data: data provided by the client
        :param DeclarativeMeta obj: the sqlalchemy object to plug relationships to
        :return boolean: True if relationship have changed else False
        """
        relationship_fields = get_relationships(self.resource.schema)
        for key, value in data.items():
            if key in relationship_fields:
                related_model = getattr(obj.__class__, key).property.mapper.class_
                related_id_field = self.resource.schema._declared_fields[relationship_fields[key]].id_field

                if isinstance(value, list):
                    related_objects = []

                    for identifier in value:
                        related_object = self.get_related_object(related_model, related_id_field, {'id': identifier})
                        related_objects.append(related_object)

                    setattr(obj, key, related_objects)
                else:
                    related_object = None

                    if value is not None:
                        related_object = self.get_related_object(related_model, related_id_field, {'id': value})

                    setattr(obj, key, related_object)

    def filter_query(self, query, filter_info, model):
        """Filter query according to jsonapi 1.0

        :param Query query: sqlalchemy query to sort
        :param filter_info: filter information
        :type filter_info: dict or None
        :param DeclarativeMeta model: an sqlalchemy model
        :return Query: the sorted query
        """
        if filter_info:
            filters = create_filters(model, filter_info, self.resource)
            query = query.filter(*filters)

        return query

    def sort_query(self, query, sort_info):
        """Sort query according to jsonapi 1.0
        :param Query query: sqlalchemy query to sort
        :param list sort_info: sort information
        :return Query: the sorted query
        """
        for sort_opt in sort_info:
            field = sort_opt['field']
            model = self.model
            if sort_opt.get('relation'):
                relation = getattr(model, sort_opt['relation'])
                query = query.join(relation)
                model = relation.property.mapper.class_
            if not hasattr(model, field):
                raise InvalidSort("{} has no attribute {}".format(self.model.__name__, field))
            query = query.order_by(getattr(getattr(model, field), sort_opt['order'])())
        return query

    def paginate_query(self, query, paginate_info):
        """Paginate query according to jsonapi 1.0

        :param Query query: sqlalchemy queryset
        :param dict paginate_info: pagination information
        :return Query: the paginated query
        """
        if int(paginate_info.get('size', 1)) == 0:
            return query

        page_size = int(paginate_info.get('size', 0)) or DEFAULT_PAGE_SIZE
        query = query.limit(page_size)
        if paginate_info.get('number'):
            query = query.offset((int(paginate_info['number']) - 1) * page_size)

        return query

    def query(self, view_kwargs):
        """Construct the base query to retrieve wanted data

        :param dict view_kwargs: kwargs from the resource view
        """
        return self.session.query(self.model)

    def before_create_object(self, data, view_kwargs):
        """Provide additional data before object creation

        :param dict data: the data validated by marshmallow
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def after_create_object(self, obj, data, view_kwargs):
        """Provide additional data after object creation

        :param obj: an object from data layer
        :param dict data: the data validated by marshmallow
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def before_get_object(self, view_kwargs):
        """Make work before to retrieve an object

        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def after_get_object(self, obj, view_kwargs):
        """Make work after to retrieve an object

        :param obj: an object from data layer
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def before_get_collection(self, qs, view_kwargs):
        """Make work before to retrieve a collection of objects

        :param QueryStringManager qs: a querystring manager to retrieve information from url
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def after_get_collection(self, collection, qs, view_kwargs):
        """Make work after to retrieve a collection of objects

        :param iterable collection: the collection of objects
        :param QueryStringManager qs: a querystring manager to retrieve information from url
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def before_update_object(self, obj, data, view_kwargs):
        """Make checks or provide additional data before update object

        :param obj: an object from data layer
        :param dict data: the data validated by marshmallow
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def after_update_object(self, obj, data, view_kwargs):
        """Make work after update object

        :param obj: an object from data layer
        :param dict data: the data validated by marshmallow
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def before_delete_object(self, obj, view_kwargs):
        """Make checks before delete object

        :param obj: an object from data layer
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def after_delete_object(self, obj, view_kwargs):
        """Make work after delete object

        :param obj: an object from data layer
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def before_create_relationship(self, json_data, relationship_field, related_id_field, view_kwargs):
        """Make work before to create a relationship

        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if relationship have changed else False
        """
        pass

    def after_create_relationship(self, obj, updated, json_data, relationship_field, related_id_field, view_kwargs):
        """Make work after to create a relationship

        :param obj: an object from data layer
        :param bool updated: True if object was updated else False
        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if relationship have changed else False
        """
        pass

    def before_get_relationship(self, relationship_field, related_type_, related_id_field, view_kwargs):
        """Make work before to get information about a relationship

        :param str relationship_field: the model attribute used for relationship
        :param str related_type_: the related resource type
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return tuple: the object and related object(s)
        """
        pass

    def after_get_relationship(self, obj, related_objects, relationship_field, related_type_, related_id_field,
                               view_kwargs):
        """Make work after to get information about a relationship

        :param obj: an object from data layer
        :param iterable related_objects: related objects of the object
        :param str relationship_field: the model attribute used for relationship
        :param str related_type_: the related resource type
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return tuple: the object and related object(s)
        """
        pass

    def before_update_relationship(self, json_data, relationship_field, related_id_field, view_kwargs):
        """Make work before to update a relationship

        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if relationship have changed else False
        """
        pass

    def after_update_relationship(self, obj, updated, json_data, relationship_field, related_id_field, view_kwargs):
        """Make work after to update a relationship

        :param obj: an object from data layer
        :param bool updated: True if object was updated else False
        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        :return boolean: True if relationship have changed else False
        """
        pass

    def before_delete_relationship(self, json_data, relationship_field, related_id_field, view_kwargs):
        """Make work before to delete a relationship

        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        """
        pass

    def after_delete_relationship(self, obj, updated, json_data, relationship_field, related_id_field, view_kwargs):
        """Make work after to delete a relationship

        :param obj: an object from data layer
        :param bool updated: True if object was updated else False
        :param dict json_data: the request params
        :param str relationship_field: the model attribute used for relationship
        :param str related_id_field: the identifier field of the related model
        :param dict view_kwargs: kwargs from the resource view
        """
        pass
