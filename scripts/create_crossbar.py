"""
CROssBAR generation through BioCypher script
"""

from bccb.uniprot_adapter import (
    Uniprot,
    UniprotNodeType,
    UniprotNodeField,
    UniprotEdgeType,
    UniprotEdgeField,
)

from bccb.ppi_adapter import (
    PPI,
    IntactEdgeField,
    BiogridEdgeField,
    StringEdgeField,
)

from bccb.interpro_adapter import (
    InterPro,
    InterProNodeField,
    InterProEdgeField,
)

from bccb.go_adapter import (
    GO,
    GONodeType,
    GOEdgeType,
    GONodeField,
    GOEdgeField,
)

from biocypher import BioCypher

# uniprot configuration
uniprot_node_types = [
    UniprotNodeType.PROTEIN,
    UniprotNodeType.GENE,
    UniprotNodeType.ORGANISM,
]

uniprot_node_fields = [
    UniprotNodeField.PROTEIN_SECONDARY_IDS,
    UniprotNodeField.PROTEIN_LENGTH,
    UniprotNodeField.PROTEIN_MASS,
    UniprotNodeField.PROTEIN_ORGANISM,
    UniprotNodeField.PROTEIN_ORGANISM_ID,
    UniprotNodeField.PROTEIN_NAMES,
    UniprotNodeField.PROTEIN_PROTEOME,
    UniprotNodeField.PROTEIN_EC,
    UniprotNodeField.PROTEIN_GENE_NAMES,
    UniprotNodeField.PROTEIN_ENSEMBL_TRANSCRIPT_IDS,
    UniprotNodeField.PROTEIN_ENSEMBL_GENE_IDS,
    UniprotNodeField.PROTEIN_ENTREZ_GENE_IDS,
    UniprotNodeField.PROTEIN_VIRUS_HOSTS,
    UniprotNodeField.PROTEIN_KEGG_IDS,
]

uniprot_edge_types = [
    UniprotEdgeType.PROTEIN_TO_ORGANISM,
    UniprotEdgeType.GENE_TO_PROTEIN,
]

uniprot_edge_fields = [
    UniprotEdgeField.GENE_ENSEMBL_GENE_ID,
]

# ppi configuration
intact_fields = [field for field in IntactEdgeField]
biogrid_fields = [field for field in BiogridEdgeField]
string_fields = [field for field in StringEdgeField]

# interpro (protein-domain edges and domain nodes) configuration
interpro_node_fields = [field for field in InterProNodeField]
interpro_edge_fields = [field for field in InterProEdgeField]

# GO configuration (Protein-GO, Domain-GO, GO-GO)
go_node_types = [GONodeType.PROTEIN, GONodeType.DOMAIN, GONodeType.BIOLOGICAL_PROCESS,
                 GONodeType.CELLULAR_COMPONENT, GONodeType.MOLECULAR_FUNCTION]
go_edge_types = [GOEdgeType.PROTEIN_TO_BIOLOGICAL_PROCESS, GOEdgeType.DOMAIN_TO_BIOLOGICAL_PROCESS,
                 GOEdgeType.DOMAIN_TO_CELLULAR_COMPONENT, GOEdgeType.DOMAIN_TO_MOLECULAR_FUNCTION] # There are more associations, however current version of BioCypher probably only supports these
go_node_fields = [field for field in GONodeField]
go_edge_fields = [field for field in GOEdgeField]

# Run build
def main():
    """
    Main function. Call individual adapters to download and process data. Build
    via BioCypher from node and edge data.
    """

    # Start biocypher
    bc = BioCypher(schema_config_path=r"config/schema_config.yaml")

    # Start uniprot adapter and load data
    uniprot_adapter = Uniprot(
        organism="9606",
        node_types=uniprot_node_types,
        node_fields=uniprot_node_fields,
        edge_types=uniprot_edge_types,
        edge_fields=uniprot_edge_fields,
        test_mode=True,
    )

    uniprot_adapter.download_uniprot_data(
        cache=True,
        retries=5,
    )

    ppi_adapter = PPI(cache=True, 
                      organism=9606, 
                      intact_fields=intact_fields, 
                      biogrid_fields=biogrid_fields,
                      string_fields=string_fields, 
                      test_mode=True)
    
    # download and process intact data
    ppi_adapter.download_intact_data()
    ppi_adapter.intact_process()

    # download and process biogrid data
    ppi_adapter.download_biogrid_data()
    ppi_adapter.biogrid_process()

    # download and process string data
    ppi_adapter.download_string_data()
    ppi_adapter.string_process()

    # Merge all ppi data
    ppi_adapter.merge_all()

    interpro_adapter = InterPro(cache=True, 
                                page_size=100, 
                                organism="9606",
                                node_fields=interpro_node_fields,
                                edge_fields=interpro_edge_fields, 
                                test_mode=True)
    
    # download domain data
    interpro_adapter.download_domain_node_data()
    interpro_adapter.download_domain_edge_data()

    # get interpro nodes and edge
    interpro_adapter.get_interpro_nodes()
    interpro_adapter.get_interpro_edges()

    go_adapter = GO(organism=9606, node_types=go_node_types, go_node_fields=go_node_fields,
                    edge_types=go_edge_types, go_edge_fields=go_edge_fields, test_mode=True)
    
    # download go data
    go_adapter.download_go_data(cache=True)

    # get go nodes and go-protein, domain-go edges
    go_adapter.get_go_nodes()
    go_adapter.get_go_edges()


    # Write uniprot nodes and edges
    bc.write_nodes(uniprot_adapter.get_nodes())

    # write ppi edges
    bc.write_edges(ppi_adapter.get_ppi_edges())
    
    # write interpro (domain) nodes
    bc.write_nodes(interpro_adapter.node_list)

    # write interpro edges (protein-domain) edges
    bc.write_edges(interpro_adapter.edge_list)

    # write GO nodes
    bc.write_nodes(go_adapter.node_list)

    # write GO edges
    bc.write_edges(go_adapter.edge_list)

    # Write import call and other post-processing
    bc.write_import_call()
    bc.summary()


if __name__ == "__main__":
    main()
