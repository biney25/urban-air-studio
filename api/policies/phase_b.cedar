// Every entity comes from verified identity and scoped immutable server records.
@id("phase-b-same-project-source-permission")
permit (
    principal is UrbanAir::User,
    action in [UrbanAir::Action::"derive", UrbanAir::Action::"solve", UrbanAir::Action::"export"],
    resource is UrbanAir::Asset
)
when {
    principal.tenant == resource.tenant
    && principal.projects.contains(resource.project)
    && (
        (action == UrbanAir::Action::"derive" && resource.rights.derive)
        || (action == UrbanAir::Action::"solve" && resource.rights.solve)
        || (action == UrbanAir::Action::"export" && resource.rights.export)
    )
};
