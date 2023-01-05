from time import time
import collections
from typing import Dict, List, Optional
from enum import Enum

from tqdm import tqdm  # progress bar
from pypath.share import curl, settings
from pypath.utils import mapping
from pypath.inputs import uniprot
from biocypher._logger import logger
from contextlib import ExitStack
from bioregistry import normalize_curie

logger.debug(f"Loading module {__name__}.")


class UniprotNodeField(Enum):
    """
    Fields of the UniProt API represented in this adapter.
    """

    # core attributes
    PROTEIN_ID = "id"
    PROTEIN_SECONDARY_IDS = "secondary_ids"
    PROTEIN_LENGTH = "length"
    PROTEIN_MASS = "mass"
    PROTEIN_ORGANISM = "organism"
    PROTEIN_ORGANISM_ID = "organism-id"
    PROTEIN_NAMES = "protein names"
    PROTEIN_PROTEOME = "proteome"
    PROTEIN_EC = "ec"
    PROTEIN_GENE_NAMES = "genes"
    PROTEIN_ENSEMBL_GENE_IDS = "database(Ensembl)"
    # xref attributes
    PROTEIN_ENTREZ_GENE_IDS = "database(GeneID)"
    PROTEIN_VIRUS_HOSTS = "virus hosts"
    PROTEIN_KEGG_IDS = "database(KEGG)"


class UniprotEdgeField(Enum):
    """
    Fields of the UniProt API represented in this adapter.
    """

    # core attributes
    PROTEIN_TO_ORGANISM = "organism-id"
    GENE_TO_PROTEIN = "database(GeneID)"


class Uniprot:
    """
    Class that downloads uniprot data using pypath and reformats it to be ready
    for import into a BioCypher database.

    Args:
        organism: organism code in NCBI taxid format, e.g. "9606" for human.

        rev: if True, it downloads reviewed entries only.

    TODO args are autogenerated; is this correct?
    """

    def __init__(
        self,
        organism="*",
        rev=True,
        node_fields: Optional[list] = None,
        edge_fields: Optional[list] = None,
    ):

        # instance variables
        # provenance
        self.data_source = "uniprot"
        self.data_version = "2022_04" # TODO get version from pypath
        self.data_licence = "CC BY 4.0"
        
        # params
        self.organism = organism
        self.rev = rev

        # class variables:
        # fields that need splitting
        self.split_fields = [
            "secondary_ids",
            "proteome",
            "genes",
            "ec",
            "database(GeneID)",
            "database(Ensembl)",
            "database(KEGG)",
        ]

        # properties of nodes
        self.protein_properties = [
            "secondary_ids",
            "length",
            "mass",
            "protein names",
            "proteome",
            "ec",
            "virus hosts",
            "organism-id",
        ]

        self.gene_properties = [
            "genes",
            "database(GeneID)",
            "database(KEGG)",
            "database(Ensembl)",
            "ensembl_gene_ids",
        ]

        self.organism_properties = ["organism"]

        # check which node fields to include
        if node_fields:

            self.node_attributes = [field.value for field in node_fields]
            self.node_types = [field.name for field in node_fields]

        else:

            # get all values from Fields enum
            self.node_attributes = [field.value for field in UniprotNodeField]
            self.node_types = [field.name for field in UniprotNodeField]

        # check which edge fields to include
        if edge_fields:
                
            self.edge_attributes = [field.value for field in edge_fields]
            self.edge_types = [field.name for field in edge_fields]

        else:
                
            # get all values from Fields enum
            self.edge_attributes = [field.value for field in UniprotEdgeField]
            self.edge_types = [field.name for field in UniprotEdgeField]

    def download_uniprot_data(
        self,
        cache=False,
        debug=False,
        retries=3,
    ):
        """
        Wrapper function to download uniprot data using pypath; used to access
        settings.

        Args:
            cache: if True, it uses the cached version of the data, otherwise
            forces download.

            debug: if True, turns on debug mode in pypath.

            retries: number of retries in case of download error.
        """

        # stack pypath context managers
        with ExitStack() as stack:

            stack.enter_context(settings.context(retries=retries))

            if debug:
                stack.enter_context(curl.debug_on())

            if not cache:
                stack.enter_context(curl.cache_off())

            self.uniprot_data_downloader()

    def uniprot_data_downloader(self):
        """
        Download uniprot data from uniprot.org through pypath.

        TODO make use of multi-field query
        """

        t0 = time()

        # download all swissprot ids
        self.uniprot_ids = list(uniprot._all_uniprots(self.organism, self.rev))

        # download attribute dicts
        self.data = {}
        for query_key in tqdm(self.node_attributes):
            self.data[query_key] = uniprot.uniprot_data(
                query_key, self.organism, self.rev
            )

            logger.debug(f"{query_key} field is downloaded")

        secondary_ids = uniprot.get_uniprot_sec(None)
        self.data["secondary_ids"] = collections.defaultdict(list)
        for sec_id in secondary_ids:
            self.data["secondary_ids"][sec_id[1]].append(sec_id[0])
        for k, v in self.data["secondary_ids"].items():
            self.data["secondary_ids"][k] = ";".join(v)

        t1 = time()
        msg = f"Acquired UniProt data in {round((t1-t0) / 60, 2)} mins."
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
            field_value = (
                field_value.replace("|", ",").replace("'", "^").strip()
            )

            # define fields that will not be splitted by semicolon
            split_dict = {"proteome": ",", "genes": " "}

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
        field_value = field_value.replace("|", ",").replace(
            "'", "^"
        )  # replace sensitive elements

        if "[Cleaved" in field_value:
            # discarding part after the "[Cleaved"
            clip_index = field_value.index("[Cleaved")
            protein_names = (
                field_value[:clip_index].replace("(Fragment)", "").strip()
            )

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
            protein_names = (
                field_value[:clip_index].replace("(Fragment)", "").strip()
            )
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
        elif "(EC" in field_value.replace("(Fragment)", ""):
            splitted = field_value.split(" (")
            protein_names = []

            for name in splitted:
                if not name.strip().startswith("EC"):
                    if not name.strip().startswith("Fragm"):
                        protein_names.append(name.rstrip(")").strip())

        elif " (" in field_value.replace("(Fragment)", ""):
            splitted = field_value.split(" (")
            protein_names = []
            for name in splitted:
                if not name.strip().startswith("Fragm"):
                    protein_names.append(name.rstrip(")").strip())

        else:
            protein_names = field_value.replace("(Fragment)", "").strip()

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
                    virus_hosts_tax_ids.append(
                        v[v.index("[") + 1 : v.index("]")].split(":")[1].strip()
                    )
            else:
                virus_hosts_tax_ids = (
                    field_value[
                        field_value.index("[") + 1 : field_value.index("]")
                    ]
                    .split(":")[1]
                    .strip()
                )

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

        listed_enst = [enst.split(" [")[0] for enst in listed_enst]

        ensg_ids = set()
        for enst_id in listed_enst:
            ensg_id = list(
                mapping.map_name(
                    enst_id.split(".")[0], "enst_biomart", "ensg_biomart"
                )
            )
            ensg_id = ensg_id[0] if ensg_id else None
            if ensg_id:
                ensg_ids.add(ensg_id)

        ensg_ids = list(ensg_ids)

        if len(ensg_ids) == 1:
            ensg_ids = ensg_ids[0]

        if len(listed_enst) == 1:
            listed_enst = listed_enst[0]

        return listed_enst, ensg_ids

    def get_nodes(self) -> List[Dict]:
        """
        Get nodes from UniProt data.
        """

        logger.info("Preparing nodes.")

        # create list of nodes
        node_list = []

        for protein in tqdm(self.uniprot_ids):
            protein_id = normalize_curie("uniprot:" + protein)
            _props = {}
            gene_id = ""
            organism_id = ""

            for arg in self.node_attributes:

                # split fields
                if arg in self.split_fields:
                    attribute_value = self.data.get(arg).get(protein)
                    if attribute_value:
                        _props[arg] = self.fields_splitter(arg, attribute_value)

                else:
                    attribute_value = self.data.get(arg).get(protein)
                    if attribute_value:
                        _props[arg] = (
                            attribute_value.replace("|", ",")
                            .replace("'", "^")
                            .strip()
                        )

                if arg == "database(Ensembl)" and arg in _props:
                    _props[arg], ensg_ids = self.ensembl_process(_props[arg])
                    if ensg_ids:
                        _props["ensembl_gene_ids"] = ensg_ids

                elif arg == "protein names":
                    _props[arg] = self.split_protein_names_field(
                        self.data.get(arg).get(protein)
                    )

                elif arg == "virus hosts":
                    attribute_value = self.split_virus_hosts_field(
                        self.data.get(arg).get(protein)
                    )
                    if attribute_value:
                        _props[arg] = attribute_value

            protein_props = dict()
            gene_props = dict()
            organism_props = dict()

            for k in _props.keys():
                # define protein_properties
                if k in self.protein_properties:
                    # make length, mass and organism-id fields integer and replace hyphen in keys
                    if k in ["length", "mass", "organism-id"]:
                        protein_props[k.replace("-", "_")] = int(
                            _props[k].replace(",", "")
                        )
                        if k == "organism-id":
                            organism_id = normalize_curie(
                                "ncbitaxon:" + _props[k]
                            )

                    # replace hyphens and spaces with underscore
                    else:
                        protein_props[
                            k.replace(" ", "_").replace("-", "_")
                        ] = _props[k]

                # if genes and database(GeneID) fields exist, define gene_properties
                elif (
                    k in self.gene_properties
                    and "genes" in _props.keys()
                    and "database(GeneID)" in _props.keys()
                ):
                    if "database" in k:
                        # make ncbi gene id as gene_id
                        if "GeneID" in k:
                            gene_id = normalize_curie("ncbigene:" + _props[k])
                        # replace parantheses in field names and make their name lowercase
                        else:
                            gene_props[
                                k.split("(")[1].split(")")[0].lower()
                            ] = _props[k]

                    else:
                        gene_props[k] = _props[k]

                # define organism_properties
                elif k in self.organism_properties:
                    organism_props[k] = _props[k]

            # source, licence, and version fields for all nodes
            protein_props["source"] = gene_props["source"] = organism_props["source"] = self.data_source
            protein_props["licence"] = gene_props["licence"] = organism_props["licence"] = self.data_licence
            protein_props["version"] = gene_props["version"] = organism_props["version"] = self.data_version

            # append related fields to protein_nodes
            node_list.append((protein_id, "protein", protein_props))

            # append related fields to gene_nodes and gene_to_protein_edges
            if gene_id:
                node_list.append((gene_id, "gene", gene_props))

            # append related fields to organism_nodes
            if organism_id:
                node_list.append((organism_id, "organism", organism_props))

        return node_list

    def get_edges(self):
        """
        Get nodes and edges from UniProt data.
        """

        logger.info("Preparing edges.")

        # create lists of edges
        edge_list = []

        for protein in tqdm(self.uniprot_ids):

            protein_id = normalize_curie("uniprot:" + protein)

            if "GENE_TO_PROTEIN" in self.edge_types:

                gene_id = self.fields_splitter(
                    "database(GeneID)",
                    self.data.get("database(GeneID)").get(protein),
                )

                if gene_id:

                    gene_id = normalize_curie("ncbigene:" + gene_id)
                    edge_list.append((None, gene_id, protein_id, "Encodes", {}))

            if "PROTEIN_TO_ORGANISM" in self.edge_types:

                organism_id = (
                    self.data.get("organism-id")
                    .get(protein)
                    .replace("|", ",")
                    .replace("'", "^")
                    .strip()
                )

                if organism_id:

                    organism_id = normalize_curie("ncbitaxon:" + organism_id)
                    edge_list.append(
                        (None, protein_id, organism_id, "Belongs_To", dict())
                    )

        if edge_list:

            return edge_list
