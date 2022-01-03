import re
import haystack
from django.conf import settings
from rest_framework import filters
from whoosh.query import Term, And
from distutils.util import strtobool
from whoosh.qparser import QueryParser
from haystack.utils import get_model_ct
from haystack.query import SearchQuerySet
from django.core.exceptions import FieldError
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from haystack.backends.whoosh_backend import WhooshSearchBackend
from haystack.constants import DJANGO_CT, ITERATOR_LOAD_PER_QUERY

from .models import Asset, ObjectPermission
from .models.object_permission import (
    get_objects_for_user,
    get_anonymous_user,
    get_models_with_object_permissions,
)


class AssetOwnerFilterBackend(filters.BaseFilterBackend):
    """
    For use with nested models of Asset.
    Restricts access to items that are owned by the current user
    """
    def filter_queryset(self, request, queryset, view):
        # Because HookLog is two level nested,
        # we need to specify the relation in the filter field
        if type(view).__name__ == "HookLogViewSet":
            fields = {"hook__asset__owner": request.user}
        else:
            fields = {"asset__owner": request.user}

        return queryset.filter(**fields)


class KpiObjectPermissionsFilter(object):
    perm_format = '%(app_label)s.view_%(model_name)s'

    def filter_queryset(self, request, queryset, view):

        user = request.user
        if user.is_superuser and view.action != 'list':
            # For a list, we won't deluge the superuser with everyone else's
            # stuff. This isn't a list, though, so return it all
            return queryset
        # Governs whether unsubscribed (but publicly discoverable) objects are
        # included. Exclude them by default
        all_public = bool(strtobool(
            request.query_params.get('all_public', 'false').lower()))

        model_cls = queryset.model
        kwargs = {
            'app_label': model_cls._meta.app_label,
            'model_name': model_cls._meta.model_name,
        }
        permission = self.perm_format % kwargs

        if user.is_anonymous():
            user = get_anonymous_user()
            # Avoid giving anonymous users special treatment when viewing
            # public objects
            owned_and_explicitly_shared = queryset.none()
        else:
            owned_and_explicitly_shared = get_objects_for_user(
                user, permission, queryset)
        public = get_objects_for_user(
            get_anonymous_user(), permission, queryset)
        if view.action != 'list':
            # Not a list, so discoverability doesn't matter
            return (owned_and_explicitly_shared | public).distinct()

        # For a list, do not include public objects unless they are also
        # discoverable
        try:
            discoverable = public.filter(discoverable_when_public=True)
        except FieldError:
            try:
                # The model does not have a discoverability setting, but maybe
                # its parent does
                discoverable = public.filter(
                    parent__discoverable_when_public=True)
            except FieldError:
                # Neither the model or its parent has a discoverability setting
                discoverable = public.none()

        if all_public:
            # We were asked not to consider subscriptions; return all
            # discoverable objects
            return (owned_and_explicitly_shared | discoverable).distinct()

        # Of the discoverable objects, determine to which the user has
        # subscribed
        try:
            subscribed = public.filter(usercollectionsubscription__user=user)
        except FieldError:
            try:
                # The model does not have a subscription relation, but maybe
                # its parent does
                subscribed = public.filter(
                    parent__usercollectionsubscription__user=user)
            except FieldError:
                # Neither the model or its parent has a subscription relation
                subscribed = public.none()

        return (owned_and_explicitly_shared | subscribed).distinct()


class RelatedAssetPermissionsFilter(KpiObjectPermissionsFilter):
    ''' Uses KpiObjectPermissionsFilter to determine which assets the user
    may access, and then filters the provided queryset to include only objects
    related to those assets. The queryset's model must be related to `Asset`
    via a field named `asset`. '''

    def filter_queryset(self, request, queryset, view):
        available_assets = super(
            RelatedAssetPermissionsFilter, self
        ).filter_queryset(
            request=request,
            queryset=Asset.objects.all(),
            view=view
        )
        return queryset.filter(asset__in=available_assets)


class SearchFilter(filters.BaseFilterBackend):
    ''' Filter objects by searching with Whoosh if the request includes a `q`
    parameter. Another parameter, `parent`, is recognized when its value is an
    empty string; this restricts the queryset to objects without parents. '''

    library_collection_pattern = re.compile(
        r'\(((?:asset_type:(?:[^ ]+)(?: OR )*)+)\) AND \(parent__uid:([^)]+)\)'
    )

    def filter_queryset(self, request, queryset, view):
        if ('parent' in request.query_params and
                request.query_params['parent'] == ''):
            # Empty string means query for null parent
            queryset = queryset.filter(parent=None)
        try:
            q = request.query_params['q']
        except KeyError:
            return queryset

        # Short-circuit some commonly used queries
        COMMON_QUERY_TO_ORM_FILTER = {
            'asset_type:block': {'asset_type': 'block'},
            'asset_type:question': {'asset_type': 'question'},
            'asset_type:template': {'asset_type': 'template'},
            'asset_type:survey': {'asset_type': 'survey'},
            'asset_type:question OR asset_type:block': {
                'asset_type__in': ('question', 'block')
            },
            'asset_type:question OR asset_type:block OR asset_type:template': {
                'asset_type__in': ('question', 'block', 'template')
            },
        }
        try:
            return queryset.filter(**COMMON_QUERY_TO_ORM_FILTER[q])
        except KeyError:
            # We don't know how to short-circuit this query; pass it along
            pass
        except FieldError:
            # The user passed a query we recognized as commonly-used, but the
            # field was invalid for the requested model
            return queryset.none()

        # Queries for library questions/blocks inside collections are also
        # common (and buggy when using Whoosh: see #1707)
        library_collection_match = self.library_collection_pattern.match(q)
        if library_collection_match:
            asset_types = [
                type_query.split(':')[1] for type_query in
                    library_collection_match.groups()[0].split(' OR ')
            ]
            parent__uid = library_collection_match.groups()[1]
            try:
                return queryset.filter(
                    asset_type__in=asset_types,
                    parent__uid=parent__uid
                )
            except FieldError:
                return queryset.none()

        # Fall back to Whoosh
        queryset_pks = list(queryset.values_list('pk', flat=True))
        if not len(queryset_pks):
            return queryset
        # 'q' means do a full-text search of the document fields, where the
        # critera are given in the Whoosh query language:
        # https://pythonhosted.org/Whoosh/querylang.html
        search_queryset = SearchQuerySet().models(queryset.model)
        search_backend = search_queryset.query.backend
        if not isinstance(search_backend, WhooshSearchBackend):
            raise NotImplementedError(
                'Only the Whoosh search engine is supported at this time')
        if not search_backend.setup_complete:
            search_backend.setup()
        # Parse the user's query
        user_query = QueryParser('text', search_backend.index.schema).parse(q)
        # Construct a query to restrict the search to the appropriate model
        filter_query = Term(DJANGO_CT, get_model_ct(queryset.model))
        # Does the search index for this model have a field that allows
        # filtering by permissions?
        haystack_index = haystack.connections[
            'default'].get_unified_index().get_index(queryset.model)
        if hasattr(haystack_index, 'users_granted_permission'):
            # Also restrict the search to records that the user can access
            filter_query &= Term(
                'users_granted_permission', request.user.username)
        with search_backend.index.searcher() as searcher:
            results = searcher.search(
                user_query,
                filter=filter_query,
                scored=False,
                sortedby=None,
                limit=None
            )
            if not results:
                # We got nothing; is the search index even valid?
                if not searcher.search(filter_query, limit=1):
                    # Thre's not a single entry in the search index for this
                    # model; assume the index is invalid and return the
                    # queryset untouched
                    return queryset
            pk_type = type(queryset_pks[0])
            results_pks = {
                # Coerce each `django_id` from unicode to the appropriate type,
                # usually `int`
                pk_type((x['django_id'])) for x in results
            }
        filter_pks = results_pks.intersection(queryset_pks)
        return queryset.filter(pk__in=filter_pks)


class KpiAssignedObjectPermissionsFilter(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        # TODO: omit objects for which the user has only a deny permission
        user = request.user
        if isinstance(request.user, AnonymousUser):
            user = get_anonymous_user()
        if user.is_superuser:
            # Superuser sees all
            return queryset
        if user.pk == settings.ANONYMOUS_USER_ID:
            # Hide permissions from anonymous users
            return queryset.none()
        """
        A regular user sees permissions for objects to which they have access.
        For example, if Alana has view access to an object owned by Richard,
        she should see all permissions for that object, including those
        assigned to other users.
        """
        possible_content_types = ContentType.objects.get_for_models(
            *get_models_with_object_permissions()
        ).values()
        result = queryset.none()
        for content_type in possible_content_types:
            # Find all the permissions assigned to the user
            permissions_assigned_to_user = ObjectPermission.objects.filter(
                content_type=content_type,
                user=user,
            )
            # Find all the objects associated with those permissions, and then
            # find all the permissions applied to all of those objects
            result |= ObjectPermission.objects.filter(
                content_type=content_type,
                object_id__in=permissions_assigned_to_user.values(
                    'object_id'
                ).distinct()
            )
        return result
