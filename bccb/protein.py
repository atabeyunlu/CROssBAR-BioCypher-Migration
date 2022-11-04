from time import time
import collections

from tqdm import tqdm  # progress bar
from pypath.share import curl
from pypath.utils import mapping
from pypath.inputs import uniprot
from biocypher._logger import logger
import biocypher

import numpy as np
import pandas as pd

logger.debug(f"Loading module {__name__}.")


class Uniprot_data:
    def __init__(self, organism='*', rev=True):
        self.organism = organism
        self.rev = rev
        self.uniprot_df = None

        self.driver = biocypher.Driver(
            offline=True,
            db_name="neo4j",
            wipe=True,
            quote_char="'",
            delimiter="\t",
            array_delimiter="|",
            user_schema_config_path="config/schema_config.yaml",
        )

    def xref_process(self, attribute_key, protein):
        # delete trailing ';' from fields containing only one id
        xrefs = self.data[attribute_key].get(protein)
        if xrefs:
            xrefs_splitted = xrefs.split(';')
            xrefs_splitted = list(filter(None, xrefs_splitted))
            if len(xrefs_splitted) > 1:
                return xrefs
            else:
                return xrefs_splitted[0]

    def ensembl_process(self, enst_list):
        # take ensembl transcript ids, return ensembl gene ids by using pypath mapping tool
        ensg_ids = set()
        for enst_id in enst_list.split(';'):
            only_transcript_id = enst_id.split(' [')[
                0
            ]  # strip from uniprot alternative transcript id
            only_transcript_id = only_transcript_id.split('.')[0]
            ensg_id = list(
                mapping.map_name(
                    only_transcript_id, 'enst_biomart', 'ensg_biomart'
                )
            )
            ensg_id = ensg_id[0] if ensg_id else None
            if ensg_id:
                ensg_ids.add(ensg_id)

        ensg_list = ';'.join(ensg_ids) if ensg_ids else None

        return ensg_list

    def uniprot_data_download(self, cache=False):
        """
        Download uniprot data from uniprot.org through pypath.

        Args:
            cache: if True, it uses the cached version of the data, otherwise
            forces download.
        """

        if not cache:
            # set curl CACHE variable to False to force pypath to download the
            # data from uniprot
            curl.CACHE = False

        t0 = time()

        self.attributes = [  # with query field keys in uniprot api
            # primary attributes
            'length',
            'mass',
            'organism',
            'organism-id',
            'protein names',
            'proteome',
            'genes',
            'ec',
            'database(Ensembl)',
            # xref attributes
            'database(GeneID)',
            'virus hosts',
            'database(KEGG)',
            'database(EnsemblBacteria)',
            'database(EnsemblFungi)',
            'database(EnsemblMetazoa)',
            'database(EnsemblPlants)',
            'database(EnsemblProtists)',
        ]

        # download all swissprot ids
        self.uniprot_ids = list(uniprot._all_uniprots(self.organism, self.rev))

        # download attribute dicts
        self.data = {}
        for query_key in tqdm(self.attributes):
            self.data[query_key] = uniprot.uniprot_data(
                query_key, self.organism, self.rev
            )
            logger.debug(f'{query_key} field is downloaded')

        secondary_ids = uniprot.get_uniprot_sec(None)
        self.data['secondary_ids'] = collections.defaultdict(list)
        for id in secondary_ids:
            self.data['secondary_ids'][id[1]].append(id[0])
        for k, v in self.data['secondary_ids'].items():
            self.data['secondary_ids'][k] = ';'.join(v)

        t1 = time()
        msg = (
            f'Downloaded data from UniProtKB in {round((t1-t0) / 60, 2)} mins.'
        )
        logger.info(msg)
    
    def fields_splitter(self, field_key, field_value):
        """
        Split fields with multiple entries in uniprot
        Args:
            field_key: field name
            field_value: entry of the field
        """
        if field_value:
            # replace sensitive elements for admin-import
            field_value = field_value.replace("|",",").replace("'","").strip()
            
            # define fields that will not be splitted by semicolon
            split_dict = {"proteome":",", "genes":" "}
            
            # if field in split_dict split accordingly
            if field_key in split_dict.keys():
                field_value = field_value.split(split_dict[field_key])
                # if field has just one element in the list make it string
                if len(field_value) == 1:
                    field_value = field_value[0]
                    
            # split semicolons (;)
            else:
                field_value = field_value.strip().strip(";").split(";")
                
                # split colons (":") in kegg field
                if field_key == "database(KEGG)":
                    _list = []
                    for e in field_value:
                        _list.append(e.split(":")[1].strip())
                    field_value = _list
                
                # take first element in database(GeneID) field
                if field_key == "database(GeneID)":
                    field_value = field_value[0]
                
                # if field has just one element in the list make it string
                if isinstance(field_value, list) and len(field_value) == 1:
                    field_value = field_value[0]
            
            return field_value
        
        else:
            return None
    
    def split_protein_names_field(self, field_value):
        """
        Split protein names field in uniprot
        Args:
            field_value: entry of the protein names field
        Example:
            "Acetate kinase (EC 2.7.2.1) (Acetokinase)" -> ["Acetate kinase", "Acetokinase"]
        """
        field_value = field_value.replace("|",",").replace("'","") # replace sensitive elements
    
        if "[Cleaved" in field_value:
            # discarding part after the "[Cleaved"
            clip_index = field_value.index("[Cleaved")
            protein_names = field_value[:clip_index].replace("(Fragment)","").strip()
        
            # handling multiple protein names
            if "(EC" in protein_names[0]:
                splitted = protein_names[0].split(" (")
                protein_names = []

                for name in splitted:                    
                    if not name.strip().startswith("EC"):
                        if not name.strip().startswith("Fragm"):
                            protein_names.append(name.rstrip(")").strip())

            elif " (" in protein_names[0]:
                splitted = protein_names[0].split(" (")
                protein_names = []
                for name in splitted:
                    if not name.strip().startswith("Fragm"):
                        protein_names.append(name.rstrip(")").strip())

        elif "[Includes" in field_value:
            # discarding part after the "[Includes"
            clip_index = field_value.index("[Includes")
            protein_names = field_value[:clip_index].replace("(Fragment)","").strip()
            # handling multiple protein names
            if "(EC" in protein_names[0]:

                splitted = protein_names[0].split(" (")
                protein_names = []

                for name in splitted:                    
                    if not name.strip().startswith("EC"):
                        if not name.strip().startswith("Fragm"):
                            protein_names.append(name.rstrip(")").strip())

            elif " (" in protein_names[0]:
                splitted = protein_names[0].split(" (")
                protein_names = []
                for name in splitted:
                    if not name.strip().startswith("Fragm"):
                        protein_names.append(name.rstrip(")").strip())                    

        # handling multiple protein names
        elif "(EC" in field_value.replace("(Fragment)",""):
            splitted = field_value.split(" (")
            protein_names = []

            for name in splitted:                
                if not name.strip().startswith("EC"):
                    if not name.strip().startswith("Fragm"):
                        protein_names.append(name.rstrip(")").strip())

        elif " (" in field_value.replace("(Fragment)",""):
            splitted = field_value.split(" (")
            protein_names = []
            for name in splitted:
                if not name.strip().startswith("Fragm"):
                    protein_names.append(name.rstrip(")").strip())                    

        else:
            protein_names = field_value.replace("(Fragment)","").strip()


        return protein_names
    
    def split_virus_hosts_field(self, field_value):
        """
        Split virus hosts fields in uniprot
        
        Args:
            field_value: entry of the virus hosts field
        
        Example:
            "Pyrobaculum arsenaticum [TaxID: 121277]; Pyrobaculum oguniense [TaxID: 99007]" -> ['121277', '99007']
        """
        if field_value:
            if ";" in field_value:
                splitted = field_value.split(";")
                virus_hosts_tax_ids = []
                for v in splitted:
                    virus_hosts_tax_ids.append(v[v.index("[")+1:v.index("]")].split(":")[1].strip())
            else:
                virus_hosts_tax_ids = field_value[field_value.index("[")+1:field_value.index("]")].split(":")[1].strip()

            return virus_hosts_tax_ids
        else:
            return None

    def ensembl_process(self, ens_list):
        """
        take ensembl transcript ids, return ensembl gene ids by using pypath mapping tool
        
        Args:
            field_value: ensembl transcript list

        """

        listed_enst = []
        if isinstance(ens_list, str):
            listed_enst.append(ens_list)

        else:
            listed_enst = ens_list
            

        listed_enst = [enst.split(' [')[0] for enst in listed_enst]
        if len(listed_enst) == 1:
            listed_enst = listed_enst[0]

        ensg_ids = set()
        for enst_id in listed_enst:
            ensg_id = list(
                mapping.map_name(
                    enst_id.split('.')[0], 'enst_biomart', 'ensg_biomart'
                )
            )
            ensg_id = ensg_id[0] if ensg_id else None
            if ensg_id:
                ensg_ids.add(ensg_id)
        ensg_ids = list(ensg_ids)

        if len(ensg_ids) == 1:
            ensg_ids = ensg_ids[0]

        return listed_enst, ensg_ids

    
    def write_uniprot_nodes(self):
        """
        Write nodes through BioCypher.
        """

        logger.info("Writing nodes to CSV for admin import")
        
        # define fields that need splitting
        split_fields = ["secondary_ids", "proteome", "genes", "ec", "database(GeneID)", "database(Ensembl)", "database(KEGG)"]
        
        # define properties of nodes
        protein_properties = ["secondary_ids", "length", "mass", "protein names", "proteome", "ec", "virus hosts", "organism-id"]
        gene_properties = ["genes", "database(GeneID)", "database(KEGG)", "database(Ensembl)", "ensembl_gene_ids"]
        organism_properties = ["organism"]
        
        # add secondary_ids to self.attributes
        attributes = self.attributes + ["secondary_ids"]
        
        # 
        gene_id = str()
        
        # create lists of nodes
        protein_nodes = []
        gene_nodes = []
        organism_nodes = []
        
        # create lists of edges
        gene_to_protein_edges = []
        protein_to_organism_edges = []

        for protein in self.uniprot_ids:
            protein_id = protein
            _props = {}


            for arg in attributes:

                # split fields
                if arg in split_fields:
                    attribute_value = self.data.get(arg).get(protein)
                    if attribute_value:
                        _props[arg] = self.fields_splitter(arg, attribute_value)

                else:
                    attribute_value = self.data.get(arg).get(protein)
                    if attribute_value:                        
                        _props[arg] = attribute_value

                if arg == 'database(Ensembl)' and arg in _props:
                    if arg in _props:
                        _props[arg], ensg_ids = self.ensembl_process(_props[arg])
                        if ensg_ids:
                            _props["ensembl_gene_ids"] = ensg_ids

                elif arg == "protein names":
                    _props[arg] = self.split_protein_names_field(self.data.get(arg).get(protein))

                elif arg == "virus hosts":                    
                    attribute_value = self.split_virus_hosts_field(self.data.get(arg).get(protein))
                    if attribute_value:                        
                        _props[arg] = attribute_value


            protein_props = dict()
            gene_props = dict()
            organism_props = dict()
            
            for k, v in _props.items():
                # define protein_properties
                if k in protein_properties:
                    # make length, mass and organism-id fields integer and replace hyphen in keys
                    if k in ["length", "mass", "organism-id"]:                       
                        protein_props[k.replace("-","_")] = int(_props[k].replace(",",""))
                        if k == "organism-id":                            
                            organism_id = _props[k]
                    
                    # replace hyphens and spaces with underscore
                    else:                        
                        protein_props[k.replace(" ","_").replace("-","_")] = _props[k]
                
                # if genes and database(GeneID) fields exist, define gene_properties
                elif k in gene_properties and "genes" in _props.keys() and "database(GeneID)" in _props.keys():                    
                    if "database" in k:
                        # make ncbi gene id as gene_id
                        if "GeneID" in k:                            
                            gene_id = _props[k]                                                    
                        # replace parantheses in field names and make their name lowercase
                        else:
                            gene_props[k.split("(")[1].split(")")[0].lower()] = _props[k]

                    else:
                        gene_props[k] = _props[k]

                
                # define organism_properties        
                elif k in organism_properties:
                    organism_props[k] = _props[k]
            
            # append related fields to protein_nodes
            protein_nodes.append((protein_id, "protein", protein_props))
            
            # append related fields to gene_nodes and gene_to_protein_edges
            if gene_props:
                gene_nodes.append((gene_id, "gene", gene_props))
                gene_to_protein_edges.append((gene_id, protein_id, "Encodes", dict()))
            
            # append related fields to organism_nodes
            organism_nodes.append((organism_id, "organism", organism_props))
            
            # append related fields to protein_to_organism_edges
            protein_to_organism_edges.append((protein_id, organism_id, "Belongs_To", dict()))
            
        
        # write nodes to admin-import compatible csvs
        self.driver.write_nodes(protein_nodes)
        self.driver.write_nodes(gene_nodes)
        self.driver.write_nodes(organism_nodes)
        
        # write edges to admin-import compatible csvs
        self.driver.write_edges(gene_to_protein_edges)
        self.driver.write_edges(protein_to_organism_edges)
        
        # write admin-import call
        self.driver.write_import_call()

    def build_dataframe(self):
        logger.debug("Building dataframe")
        protein_dict_list = []
        for protein in tqdm(self.uniprot_ids):
            protein_dict = {
                # define primary attributes which needs specific preprocessing
                # 'accession': protein,
                # 'secondary_accessions': self.data['secondary_ids'].get(
                #     protein
                # ),
                # 'length': int(self.data['length'].get(protein)),
                # 'mass': (
                #     int(self.data['mass'][protein].replace(',', ''))
                #     if protein in self.data['mass']
                #     else None
                # ),
                # 'tax_id': int(self.data['organism-id'].get(protein)),
                # 'organism': self.data['organism'].get(protein),
                # 'protein_names': self.data['protein names'].get(protein),
                'chromosome': (  # TODO why
                    ';'.join(self.data['proteome'].get(protein).split(','))
                    if self.data['proteome'].get(protein)
                    else None
                ),
                'genes': (  # TODO why
                    ';'.join(self.data['genes'][protein].split(' '))
                    if protein in self.data['genes']
                    else None
                ),
                # 'ec_numbers': self.data['ec'].get(protein),
                'ensembl_transcript': self.xref_process(  # TODO is this an edge?
                    'database(Ensembl)', protein
                ),
                'ensembl_gene': (  # TODO is this an edge?
                    self.ensembl_process(
                        self.xref_process('database(Ensembl)', protein)
                    )
                    if self.xref_process('database(Ensembl)', protein)
                    else None
                ),
            }

            protein_dict_list.append(protein_dict)

        self.uniprot_df = pd.DataFrame.from_dict(
            protein_dict_list, orient='columns'
        )  # create uniprot dataframe

        self.uniprot_df.replace("nan", np.nan, inplace=True)
        self.uniprot_df.fillna(
            value=np.nan, inplace=True
        )  # replace None with NaN
        logger.info("Dataframe is built")

    def generate_nodes_and_edges(self, uniprot_df=None, early_stopping=None):

        if uniprot_df is None:
            uniprot_df = self.uniprot_df

        node_values = [
            "accession",
            "secondary_accessions",
            "length",
            "mass",
            "tax_id",
            "organism",
            "protein_names",
            "chromosome_ids",
            "chromosome_info",
            "entrez_id",
            "ec_numbers",
            "kegg",
            "ensembl_transcript",
            "ensembl_gene_ids",
            "genes",
            "virus_hosts_tax_ids",
        ]  # list of values that will be added graph database

        keys = list(range(0, len(node_values)))
        order = dict()
        for k, v in zip(keys, node_values):
            order[k] = v

        # attributes of nodes (protein, gene, organism)
        protein_attributes_list = [
            "accession",
            "secondary_accessions",
            "length",
            "mass",
            "protein_names",
            "chromosome_ids",
            "chromosome_info",
            "tax_id",
            "ec_numbers",
            "virus_hosts_tax_ids",
        ]
        organism_attributes_list = ["tax_id", "organism"]
        gene_attributes_list = [
            "entrez_id",
            "genes",
            "kegg",
            "ensembl_transcript",
            "ensembl_gene_ids",
            "tax_id",
        ]

        # lists for checking duplicates and creating nodes
        protein_check_duplicates_admin_import = []
        gene_check_duplicates_admin_import = []
        organism_check_duplicates_admin_import = []
        entrez_id_check_duplicates = []

        # lists for creating edges
        gene_to_protein_interactions = []
        protein_to_organism_interactions = []

        for index, row in tqdm(uniprot_df.iterrows()):

            if early_stopping is not None and index == early_stopping:
                break

            (
                accession,
                secondary_accessions,
                length,
                mass,
                tax_id,
                organism,
                protein_names,
                chromosome_ids,
                chromosome_info,
                entrez_id,
                ec_numbers,
                kegg,
                ensembl_transcript,
                ensemble_gene_ids,
                genes,
                virus_hosts_tax_ids,
            ) = self.clean_uniprot_data_for_nodes(row)

            all_values = [
                accession,
                secondary_accessions,
                length,
                mass,
                tax_id,
                organism,
                protein_names,
                chromosome_ids,
                chromosome_info,
                entrez_id,
                ec_numbers,
                kegg,
                ensembl_transcript,
                ensemble_gene_ids,
                genes,
                virus_hosts_tax_ids,
            ]

            # check if there is NaN, if not add them belonging nodes (i.e., protein, gene, organism)
            protein_add_dict = {}
            gene_add_dict = {}
            organism_add_dict = {}
            for idx, v in enumerate(all_values):
                if order[idx] in protein_attributes_list:
                    protein_add_dict[order[idx]] = v
                if order[idx] in gene_attributes_list:
                    gene_add_dict[order[idx]] = v
                if order[idx] in organism_attributes_list:
                    organism_add_dict[order[idx]] = v

            # replace sensitive elements for admin-import csv from protein node
            for k, v in protein_add_dict.items():
                if isinstance(v, list):
                    if len(v) == 1:
                        protein_add_dict[k] = (
                            str(v[0])
                            .replace(";", ":")
                            .replace("|", ",")
                            .replace("'", "")
                        )
                    else:
                        v_update = [
                            str(e).replace("|", ",").replace("'", "")
                            for e in v
                        ]
                        protein_add_dict[k] = "|".join(v_update).replace(
                            ";", ":"
                        )

                elif isinstance(v, str):
                    protein_add_dict[k] = (
                        str(v)
                        .replace(";", ":")
                        .replace("|", ",")
                        .replace("'", "")
                    )

            # replace sensitive elements for admin-import csv from gene node
            for k, v in gene_add_dict.items():
                if isinstance(v, list):
                    if len(v) == 1:
                        gene_add_dict[k] = (
                            str(v[0])
                            .replace(";", ":")
                            .replace("|", ",")
                            .replace("'", "")
                        )
                    else:
                        v_update = [
                            str(e).replace("|", ",").replace("'", "")
                            for e in v
                        ]
                        gene_add_dict[k] = "|".join(v_update).replace(";", ":")

                elif isinstance(v, str):
                    gene_add_dict[k] = (
                        str(v)
                        .replace(";", ":")
                        .replace("|", ",")
                        .replace("'", "")
                    )

            # replace sensitive elements for admin-import csv from organism node
            for k, v in organism_add_dict.items():
                organism_add_dict[k] = str(v).replace("'", "")

            # create protein-organism edge
            protein_to_organism_interaction_dict = {
                "accession": accession,
                "tax_id": tax_id,
            }
            protein_to_organism_interactions.append(
                protein_to_organism_interaction_dict
            )

            # check duplicates in protein node, if not add it to list
            if protein_add_dict not in protein_check_duplicates_admin_import:
                protein_check_duplicates_admin_import.append(protein_add_dict)

            # check genes and entrez_id is whether NaN and check gene node whether duplicate, if not create gene node
            if (
                str(genes) != "nan"
                and str(entrez_id) != "nan"
                and gene_add_dict not in gene_check_duplicates_admin_import
                and entrez_id not in entrez_id_check_duplicates
            ):
                entrez_id_check_duplicates.append(entrez_id)
                gene_check_duplicates_admin_import.append(gene_add_dict)

            # create gene-protein edge
            if str(genes) != "nan" and str(entrez_id) != "nan":
                gene_to_protein_interaction_dict = {
                    "entrez_id": entrez_id,
                    "accession": accession,
                }
                gene_to_protein_interactions.append(
                    gene_to_protein_interaction_dict
                )

            # create organism node if not duplicate
            if organism_add_dict not in organism_check_duplicates_admin_import:
                organism_check_duplicates_admin_import.append(
                    organism_add_dict
                )

        return (
            protein_check_duplicates_admin_import,
            gene_check_duplicates_admin_import,
            organism_check_duplicates_admin_import,
            gene_to_protein_interactions,
            protein_to_organism_interactions,
        )

    def clean_uniprot_data_for_nodes(self, row):
        # accession
        # CURRENT FORMAT:STR
        # returns accession
        accession = str(row["accession"])

        # secondary_accessions
        # CURRENT FORMAT:LIST
        # returns secondary_accessions
        if str(row["secondary_accessions"]) == "nan":
            secondary_accessions = np.nan
        elif ";" in str(row["secondary_accessions"]):
            secondary_accessions = (
                str(row["secondary_accessions"]).strip().split(";")
            )
        else:
            secondary_accessions = [str(row["secondary_accessions"]).strip()]

        # length
        # CURRENT FORMAT:INT (MAY CHANGE)
        # returns length
        length = int(row["length"])

        # mass
        # CURRENT FORMAT:INT (MAY CHANGE)
        # returns mass
        mass = int(row["mass"])

        # tax_id
        # CURRENT FORMAT:INT (MAY CHANGE)
        # returns tax_id
        tax_id = int(row["tax_id"])

        # organism
        # CURRENT FORMAT:STR
        # returns organism
        if str(row["organism"]) == "nan":
            organism = np.nan
        else:
            organism = str(row["organism"])

        # protein_names
        # CURRENT FORMAT:LIST
        # returns protein_names
        if "[Cleaved" in str(row["protein_names"]):
            # discarding part after the "[Cleaved"
            clip_index = str(row["protein_names"]).index("[Cleaved")
            protein_names = [
                str(row["protein_names"])[:clip_index]
                .replace("(Fragment)", "")
                .strip()
            ]

            # handling multiple protein names
            if "(EC" in protein_names[0]:
                splitted = protein_names[0].split(" (")
                protein_names = []

                for name in splitted:
                    if not name.strip().startswith("EC"):
                        if not name.strip().startswith("Fragm"):
                            if name.strip().endswith(")"):
                                protein_names.append(
                                    name.replace(")", "").strip()
                                )
                            else:
                                protein_names.append(name.strip())

            elif " (" in protein_names[0]:
                splitted = protein_names[0].split(" (")
                protein_names = []
                for name in splitted:
                    if not name.strip().startswith("Fragm"):
                        if name.strip().endswith(")"):
                            protein_names.append(name.replace(")", "").strip())
                        else:
                            protein_names.append(name.strip())

        elif "[Includes" in str(row["protein_names"]):
            # discarding part after the "[Includes"
            clip_index = str(row["protein_names"]).index("[Includes")
            protein_names = [
                str(row["protein_names"])[:clip_index]
                .replace("(Fragment)", "")
                .strip()
            ]
            # handling multiple protein names
            if "(EC" in protein_names[0]:

                splitted = protein_names[0].split(" (")
                protein_names = []

                for name in splitted:
                    if not name.strip().startswith("EC"):
                        if not name.strip().startswith("Fragm"):
                            if name.strip().endswith(")"):
                                protein_names.append(
                                    name.replace(")", "").strip()
                                )
                            else:
                                protein_names.append(name.strip())

            elif " (" in protein_names[0]:
                splitted = protein_names[0].split(" (")
                protein_names = []
                for name in splitted:
                    if not name.strip().startswith("Fragm"):
                        if name.strip().endswith(")"):
                            protein_names.append(name.replace(")", "").strip())
                        else:
                            protein_names.append(name.strip())

        # handling multiple protein names
        elif "(EC" in str(row["protein_names"]).replace("(Fragment)", ""):
            splitted = str(row["protein_names"]).split(" (")
            protein_names = []

            for name in splitted:
                if not name.strip().startswith("EC"):
                    if not name.strip().startswith("Fragm"):
                        if name.strip().endswith(")"):
                            protein_names.append(name.replace(")", "").strip())
                        else:
                            protein_names.append(name.strip())

        elif " (" in str(row["protein_names"]).replace("(Fragment)", ""):
            splitted = str(row["protein_names"]).split(" (")
            protein_names = []
            for name in splitted:
                if not name.strip().startswith("Fragm"):
                    if name.strip().endswith(")"):
                        protein_names.append(name.replace(")", "").strip())
                    else:
                        protein_names.append(name.strip())

        else:
            protein_names = [
                str(row["protein_names"]).replace("(Fragment)", "").strip()
            ]

        # chromosome
        # CURRENT FORMAT:LIST
        # returns chromosome_ids, chromosome_info
        if str(row["chromosome"]) == "nan":
            chromosome_ids = np.nan
            chromosome_info = np.nan

        elif ";" in str(row["chromosome"]):
            ch_list = str(row["chromosome"]).split(";")
            chromosome_ids = []
            chromosome_info = []
            for pair in ch_list:
                if len(pair.split(":")) == 2:
                    chromosome_ids.append(pair.split(":")[0].strip())
                    chromosome_info.append(pair.split(":")[1].strip())

            if chromosome_info.count("Chromosome") > 1:
                chromosome_info = list(set(chromosome_info))

        else:
            chromosome_ids = [str(row["chromosome"]).split(":")[0].strip()]
            chromosome_info = [str(row["chromosome"]).split(":")[1].strip()]

        # genes
        # CURRENT FORMAT:LIST
        # returns genes
        if str(row["genes"]) == "nan":
            genes = np.nan
        elif ";" in str(row["genes"]):
            genes = str(row["genes"]).split(";")
        else:
            genes = [str(row["genes"])]

        # ec_numbers
        # CURRENT FORMAT:LIST
        # returns ec_numbers
        if str(row["ec_numbers"]) == "nan":
            ec_numbers = np.nan
        elif ";" in str(row["ec_numbers"]):
            ec_numbers = [e.strip() for e in str(row["ec_numbers"]).split(";")]
        else:
            ec_numbers = [str(row["ec_numbers"]).strip()]

        # kegg
        # CURRENT FORMAT:LIST
        # returns kegg
        if str(row["database(KEGG)"]) == "nan":
            kegg = np.nan
        elif ";" in str(row["database(KEGG)"]):
            splitted = str(row["database(KEGG)"])[:-1].split(";")
            kegg = []
            for pair in splitted:
                if str(genes) == "nan":
                    kegg.append(pair.split(":")[1].strip())
                elif pair.split(":")[1].strip() in genes:
                    kegg.append(pair.split(":")[1].strip())
                else:
                    kegg.append(pair.split(":")[1].strip())
        else:
            kegg = [str(row["database(KEGG)"]).split(":")[1].strip()]

        # ensembl_transcript
        # CURRENT FORMAT:LIST
        # returns ensembl_transcript
        if str(row["ensembl_transcript"]) == "nan":
            ensembl_transcript = np.nan
        elif ";" in str(row["ensembl_transcript"]):
            splitted = str(row["ensembl_transcript"])[:-1].split(";")
            ensembl_transcript = [tr.strip() for tr in splitted]
        else:
            ensembl_transcript = [str(row["ensembl_transcript"]).strip()]

        # ensembl_gene
        # CURRENT FORMAT:LIST
        # returns ensembl_gene
        if str(row["ensembl_gene"]) == "nan":
            ensembl_gene = np.nan
        elif ";" in str(row["ensembl_gene"]):
            splitted = str(row["ensembl_gene"]).split(";")
            ensembl_gene = [g.strip() for g in splitted]
        else:
            ensembl_gene = [str(row["ensembl_gene"]).strip()]

        # entrez_id
        # CURRENT FORMAT:STR
        # returns entrez_id
        if str(row["database(GeneID)"]) == "nan":
            entrez_id = np.nan
        elif ";" in str(row["database(GeneID)"]):
            splitted = str(row["database(GeneID)"])[:-1].split(";")
            entrez_id = [ent.strip() for ent in splitted]
            if (
                str(row["database(DisGeNET)"]) != "nan"
                and ";" not in str(row["database(DisGeNET)"])
                and str(row["database(DisGeNET)"]) in entrez_id
            ):
                entrez_id = str(
                    row["database(DisGeNET)"]
                )  # takes the DisGeNET's entrez_id as id
            else:
                entrez_id = str(
                    entrez_id[0]
                )  # takes first element as entrez_id
        else:
            entrez_id = str(row["database(GeneID)"]).strip()

        # EnsemblBacteria
        # CURRENT FORMAT:LIST
        # returns EnsemblBacteria
        if str(row["database(EnsemblBacteria)"]) == "nan":
            EnsemblBacteria = np.nan
        elif ";" in str(row["database(EnsemblBacteria)"]):
            splitted = str(row["database(EnsemblBacteria)"])[:-1].split(";")
            EnsemblBacteria = [ens.strip() for ens in splitted]
        else:
            EnsemblBacteria = [str(row["database(EnsemblBacteria)"]).strip()]

        # EnsemblFungi
        # CURRENT FORMAT: LIST
        # returns EnsemblFungi
        if str(row["database(EnsemblFungi)"]) == "nan":
            EnsemblFungi = np.nan
        elif ";" in str(row["database(EnsemblFungi)"]):
            splitted = str(row["database(EnsemblFungi)"])[:-1].split(";")
            EnsemblFungi = [ens.strip() for ens in splitted]
        else:
            EnsemblFungi = [str(row["database(EnsemblFungi)"]).strip()]

        # EnsemblMetazoa
        # CURRENT FORMAT: LIST
        # returns EnsemblMetazoa
        if str(row["database(EnsemblMetazoa)"]) == "nan":
            EnsemblMetazoa = np.nan
        elif ";" in str(row["database(EnsemblMetazoa)"]):
            splitted = str(row["database(EnsemblMetazoa)"])[:-1].split(";")
            EnsemblMetazoa = [ens.strip() for ens in splitted]
        else:
            EnsemblMetazoa = [str(row["database(EnsemblMetazoa)"]).strip()]

        # EnsemblPlants
        # CURRENT FORMAT: LIST
        # returns EnsemblPlants
        if str(row["database(EnsemblPlants)"]) == "nan":
            EnsemblPlants = np.nan
        elif ";" in str(row["database(EnsemblPlants)"]):
            splitted = str(row["database(EnsemblPlants)"])[:-1].split(";")
            EnsemblPlants = [ens.strip() for ens in splitted]
        else:
            EnsemblPlants = [str(row["database(EnsemblPlants)"]).strip()]

        # EnsemblProtists
        # CURRENT FORMAT: LIST
        # returns EnsemblProtists
        if str(row["database(EnsemblProtists)"]) == "nan":
            EnsemblProtists = np.nan
        elif ";" in str(row["database(EnsemblProtists)"]):
            splitted = str(row["database(EnsemblProtists)"])[:-1].split(";")
            EnsemblProtists = [ens.strip() for ens in splitted]
        else:
            EnsemblProtists = [str(row["database(EnsemblProtists)"]).strip()]

        # virus_hosts
        # CURRENT FORMAT:LIST
        # returns virus_hosts_tax_ids
        if str(row["virus hosts"]) == "nan":
            virus_hosts_tax_ids = np.nan
        elif ";" in str(row["virus hosts"]):
            splitted = str(row["virus hosts"]).split(";")
            virus_hosts_tax_ids = []
            for v in splitted:
                virus_hosts_tax_ids.append(
                    v[v.index("[") + 1 : v.index("]")].split(":")[1].strip()
                )
        else:
            virus_hosts = str(row["virus hosts"])
            virus_hosts_tax_ids = [
                virus_hosts[
                    virus_hosts.index("[") + 1 : virus_hosts.index("]")
                ]
                .split(":")[1]
                .strip()
            ]

        # if there are multiple ensemble_gene ids reduce them with Opentargets
        if (
            str(ensembl_gene) != "nan"
            and len(ensembl_gene) > 1
            and str(row["database(OpenTargets)"]) != "nan"
            and ";" not in str(row["database(OpenTargets)"])
            and str(row["database(OpenTargets)"]) in ensembl_gene
        ):
            ensembl_gene = [str(row["database(OpenTargets)"])]

        # concat all ensemble gene ids
        ensemble_gene_ids = []
        concat_list = [
            ensembl_gene,
            EnsemblBacteria,
            EnsemblFungi,
            EnsemblMetazoa,
            EnsemblPlants,
            EnsemblProtists,
        ]
        for element in concat_list:
            if str(element) != "nan":
                ensemble_gene_ids.extend(element)

        if len(ensemble_gene_ids) == 0:  # if nothing in there make it NaN
            ensemble_gene_ids = np.nan

        logger.debug("accession:", accession)
        logger.debug("secondary_accessions:", secondary_accessions)
        logger.debug("length:", length)
        logger.debug("mass:", mass)
        logger.debug("tax_id:", tax_id)
        logger.debug("organism:", organism)
        logger.debug("before protein_names:", str(row["protein_names"]))
        logger.debug("protein_names:", protein_names)
        logger.debug("chromosome_ids:", chromosome_ids)
        logger.debug("chromosome_info:", chromosome_info)
        logger.debug("genes:", genes)
        logger.debug("ec_numbers:", ec_numbers)
        logger.debug("kegg:", kegg)
        logger.debug("ensembl_transcript:", ensembl_transcript)
        logger.debug("all ensemble ids:", ensemble_gene_ids)
        logger.debug("entrez_id:", entrez_id)
        logger.debug("before virus_hosts:", str(row["virus hosts"]))
        logger.debug("virus_hosts_tax_ids:", virus_hosts_tax_ids)

        return (
            accession,
            secondary_accessions,
            length,
            mass,
            tax_id,
            organism,
            protein_names,
            chromosome_ids,
            chromosome_info,
            entrez_id,
            ec_numbers,
            kegg,
            ensembl_transcript,
            ensemble_gene_ids,
            genes,
            virus_hosts_tax_ids,
        )

    def build_nodes_and_edges(self, early_stopping=None):
        """
        if "early_stopping" is specified with an integer value, it stops preprocessing on the specified row.
        """
        logger.info("Generating nodes and edges")

        # generate nodes and edges
        (
            protein_nodes,
            gene_nodes,
            organism_nodes,
            gene_to_protein_edges,
            protein_to_organism_edges,
        ) = self.generate_nodes_and_edges(
            uniprot_df=self.uniprot_df, early_stopping=early_stopping
        )

        # concatenate them into a single node and edge variable
        self.nodes = protein_nodes + gene_nodes + organism_nodes
        self.edges = gene_to_protein_edges + protein_to_organism_edges
